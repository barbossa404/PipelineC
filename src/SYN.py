#!/usr/bin/env python

import sys
import os
import subprocess
import math
import copy
import pickle
import glob
import hashlib
import getpass
from multiprocessing.pool import ThreadPool

import C_TO_LOGIC
import VHDL
import SW_LIB
import MODELSIM
import VIVADO
import VHDL_INSERT


SYN_OUTPUT_DIRECTORY="/home/" + getpass.getuser() + "/pipelinec_syn_output"
LLS_CACHE_DIR="./logic_levels_cache"
INF_MHZ = 1000 # Impossible timing goal

DO_SYN_FAIL_SIM = False

SLICE_STEPS_BETWEEN_REGS = 3 # Experimentally 2 isn't enough, slices shift <0 , > 1 easily....what?


class TimingParams:
	def __init__(self,logic):
		self.logic = logic
		self.slices = []
		self.stage_to_last_abs_submodule_level = dict()
		
		# Cached stuff
		self.calcd_total_latency = None
		self.hash_ext = None
		self.timing_report_stage_range = None
		
	def INVALIDATE_CACHE(self):
		self.calcd_total_latency = None
		self.hash_ext = None
		self.timing_report_stage_range = None
		
	def GET_ABS_STAGE_RANGE_FROM_TIMING_REPORT(self, parsed_timing_report, parser_state, TimingParamsLookupTable):
		if self.timing_report_stage_range is None:
			self.timing_report_stage_range = VIVADO.FIND_ABS_STAGE_RANGE_FROM_TIMING_REPORT(parsed_timing_report, self.logic, parser_state, TimingParamsLookupTable)
		#else:
		#	print "Using cache!"
		return self.timing_report_stage_range
		
	# I was dumb and used get latency all over
	# mAKE CACHED VERSION
	def GET_TOTAL_LATENCY(self, parser_state, TimingParamsLookupTable=None):
		if self.calcd_total_latency is None:
			self.calcd_total_latency = self.CALC_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
		#else:
		#	print "Using cache!", self.logic.inst_name, "calcd_total_latency", self.calcd_total_latency
		return self.calcd_total_latency
		
	# Haha why uppercase everywhere ...
	def CALC_TOTAL_LATENCY(self, parser_state, TimingParamsLookupTable=None):
		# C built in has multiple shared latencies based on where used
		if len(self.logic.submodule_instances) <= 0:
			return len(self.slices)
		
		if TimingParamsLookupTable is None:
			print "Need TimingParamsLookupTable for non raw hdl latency"
			print 0/0
			sys.exit(0)
			
		pipeline_map = GET_PIPELINE_MAP(self.logic, parser_state, TimingParamsLookupTable)
		latency = pipeline_map.num_stages - 1
		return latency
		
	def GET_HASH_EXT(self, TimingParamsLookupTable, parser_state):
		if self.hash_ext is None:
			self.hash_ext = BUILD_HASH_EXT(self.logic, TimingParamsLookupTable, parser_state)
		
		return self.hash_ext
		
	def ADD_SLICE(self, slice_point):			
		if slice_point > 1.0:
			print "Slice > 1.0?"
			sys.exit(0)
			slice_point = 1.0
		if slice_point < 0.0:
			print "Slice < 0.0?"
			sys.exit(0)
			slice_point = 0.0

		if not(slice_point in self.slices):
			self.slices.append(slice_point)
		self.slices = sorted(self.slices)
		self.INVALIDATE_CACHE()
		
		if self.calcd_total_latency is not None:
			print "WTF adding a slice and has latency cache?",self.calcd_total_latency
			print 0/0
			sys.exit(0)
		
	def SET_RAW_HDL_SLICE(self, slice_index, slice_point):
		self.slices[slice_index]=slice_point
		self.slices = sorted(self.slices)
		self.INVALIDATE_CACHE()
		
	def GET_SUBMODULE_LATENCY(self, submodule_inst_name, parser_state, TimingParamsLookupTable):
		submodule_timing_params = TimingParamsLookupTable[submodule_inst_name]
		return submodule_timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
		
		

def GET_ZERO_CLK_PIPELINE_MAP(Logic, parser_state):
	# Populate table as all 0 clk
	ZeroClockLogicInst2TimingParams = dict()
	for logic_inst_name in parser_state.LogicInstLookupTable: 
		logic_i = parser_state.LogicInstLookupTable[logic_inst_name]
		timing_params_i = TimingParams(logic_i)
		ZeroClockLogicInst2TimingParams[logic_inst_name] = timing_params_i
	
	# Get params for this logic
	#print "Logic.inst_name",Logic.inst_name
	#print "Logic.func_name",Logic.func_name
	#zero_clk_timing_params = ZeroClockLogicInst2TimingParams[Logic.inst_name]

	# Get pipeline map
	zero_clk_pipeline_map = GET_PIPELINE_MAP(Logic, parser_state, ZeroClockLogicInst2TimingParams)
	
	return zero_clk_pipeline_map

class PipelineMap:
	def __init__(self):
		# Any logic will have
		self.per_stage_texts = dict() # VHDL texts dict[stage_num]=[per,submodule,level,texts]
		self.stage_ordered_submodule_list = dict()
		self.num_stages = 1 # Comb logic
		# FOR NOW DOES NOT INCLUDE 0 LL SUBMODULES 
		self.stage_per_ll_submodules_map = dict() # dict[stage] => dict[ll_offset] => [submodules,at,offset]
		self.stage_per_ll_submodule_level_map = dict() # dict[stage] => dict[ll_offset] => submodule level
		self.submodule_start_offset = dict() # dict[submodule_inst] => dict[stage] = start_offset  # In LLs
		self.submodule_end_offset = dict() # dict[submodule_inst] => dict[stage] = end_offset # In LLs
		self.max_lls_per_stage = dict()

# This code is so dumb that I dont want to touch it
# Forgive me
# im such a bitch
#                   .---..---.                                                                                
#               .--.|   ||   |                                                                                
#        _     _|__||   ||   |                                                                                
#  /\    \\   //.--.|   ||   |                                                                                
#  `\\  //\\ // |  ||   ||   |                                                                                
#    \`//  \'/  |  ||   ||   |                                                                                
#     \|   |/   |  ||   ||   |                                                                                
#      '        |  ||   ||   |                                                                                
#               |__||   ||   |                                                                                
#                   '---''---'                                                                                
#                                                                                                             
#                                                                                                                                                                                                                     
#               .---.                                                                                         
# .--.          |   |.--..----.     .----.   __.....__                                                        
# |__|          |   ||__| \    \   /    /.-''         '.                                                      
# .--.          |   |.--.  '   '. /'   //     .-''"'-.  `.                                                    
# |  |          |   ||  |  |    |'    //     /________\   \                                                   
# |  |          |   ||  |  |    ||    ||                  |                                                   
# |  |          |   ||  |  '.   `'   .'\    .-------------'                                                   
# |  |          |   ||  |   \        /  \    '-.____...---.                                                   
# |__|          |   ||__|    \      /    `.             .'                                                    
#               '---'         '----'       `''-...... -'                                                      
#                                                                                                                                                                                                                     
#               .-'''-.                                                                         .-''''-..     
#              '   _    \                                                                     .' .'''.   `.   
#            /   /` '.   \               __.....__   .----.     .----.   __.....__           /    \   \    `. 
#      _.._ .   |     \  '           .-''         '.  \    \   /    /.-''         '.         \    '   |     | 
#    .' .._||   '      |  '.-,.--.  /     .-''"'-.  `. '   '. /'   //     .-''"'-.  `. .-,.--.`--'   /     /  
#    | '    \    \     / / |  .-. |/     /________\   \|    |'    //     /________\   \|  .-. |    .'  ,-''   
#  __| |__   `.   ` ..' /  | |  | ||                  ||    ||    ||                  || |  | |    |  /       
# |__   __|     '-...-'`   | |  | |\    .-------------''.   `'   .'\    .-------------'| |  | |    | '        
#    | |                   | |  '-  \    '-.____...---. \        /  \    '-.____...---.| |  '-     '-'        
#    | |                   | |       `.             .'   \      /    `.             .' | |        .--.        
#    | |                   | |         `''-...... -'      '----'       `''-...... -'   | |       /    \       
#    | |                   |_|                                                         |_|       \    /       
#    |_|
# 
def GET_PIPELINE_MAP(logic, parser_state, TimingParamsLookupTable):
	# RAW HDL doesnt need this
	if len(logic.submodule_instances) <= 0 and logic.func_name != "main":
		print "DONT USE GET_PIPELINE_MAP ON RAW HDL LOGIC!"
		print 0/0
		sys.exit(0)
	
	#print "logic.func_name",logic.func_name
	#print "logic.inst_name",logic.inst_name
	has_lls = logic.total_logic_levels is not None
	
	#print "has_lls",has_lls
	LogicInstLookupTable = parser_state.LogicInstLookupTable
	timing_params = TimingParamsLookupTable[logic.inst_name]
	is_zero_clk = len(timing_params.slices) == 0
	rv = PipelineMap()
	
	#print "timing_params.stage_to_last_abs_submodule_level",timing_params.stage_to_last_abs_submodule_level
	
	# Try to use cached pipeline map
	'''
	cached_pipeline_map = SYN.GET_CACHED_PIPELINE_MAP(logic, TimingParamsLookupTable, LogicInstLookupTable)
	if not(cached_pipeline_map is None):
		#print "Using cached pipeline map and submodule levels lookup (pipeline map)..."
		rv = cached_pipeline_map
		return rv
	'''
	
	# Else do logic below
	
	
	# FORGIVE ME - never
	print_debug = False
	
	if print_debug:
		print "==============================Getting pipline map======================================="


	# Apon writing a submoudle do not 
	# add output wire (and driven by output wires) to wires_driven_so_far
	# Intead delay them for N clocks as counted by the clock loop
	wire_to_remaining_clks_before_driven = dict()
	# All wires start with invalid latency as to not write them too soon
	for wire in logic.wires:
		wire_to_remaining_clks_before_driven[wire] = -1
	
	# To keep track of 'execution order' do this stupid thing:
	# Keep a list of wires that are have been driven so far
	# Search this list to filter which submodules are in each level
	wires_driven_so_far = []
	wires_driven_so_far = wires_driven_so_far + logic.inputs + logic.global_wires
	fully_driven_submodule_inst_name_2_logic = dict() # Keep track of sumodules done so far
	
	# Also "CONST" wires representing constants like '2' are already driven
	for wire in logic.wires:
		#print "wire=",wire
		if C_TO_LOGIC.WIRE_IS_CONSTANT(wire, logic.inst_name):
			#print "CONST->",wire
			#print wires_driven_so_far
			wires_driven_so_far.append(wire)
			wire_to_remaining_clks_before_driven[wire] = 0
	
	# Keep track of LLs offset when wire is driven
	lls_offset_when_driven = dict()
	for wire_driven_so_far in wires_driven_so_far:
		lls_offset_when_driven[wire_driven_so_far] = 0 # Is per stage value but we know is start of stage 0 
	
	# Start with wires that have drivers
	last_wires_starting_level = None
	wires_starting_level=wires_driven_so_far[:] 
	next_wires_to_follow=[]
	
	#submodule_names=[]
	#next_submodule_names=[]

	# Bound on latency is sum of all submodule latencies
	# Prevent infintie loop
	'''
	max_possible_latency = 0
	for submodule_inst in logic.submodule_instances:
		latency = timing_params.GET_SUBMODULE_LATENCY(submodule_inst, parser_state, TimingParamsLookupTable)
		max_possible_latency += latency
	max_possible_latency_with_extra = max_possible_latency + 5
	'''
	max_possible_latency = len(timing_params.slices)
	max_possible_latency_with_extra = len(timing_params.slices) + 2
	
	
	abs_submodule_level = 0
	stage_num = 0
	# WHILE LOOP FOR MULTI STAGE/CLK
	while not(logic.outputs[0] in wires_driven_so_far) :
		
		# Print stuff and set debug if obviously wrong
		if (stage_num >= max_possible_latency_with_extra):
			print "'",logic.outputs[0],"'NOT in wires_driven_so_far"
			C_TO_LOGIC.PRINT_DRIVER_WIRE_TRACE(logic.outputs[0], logic)	
			print "Something is wrong here, infinite loop probably..."
			print "logic.inst_name", logic.inst_name
			print 0/0
			sys.exit(0)
		elif (stage_num >= max_possible_latency+1):
			print_debug = True
		
			
		if print_debug:
			print "STAGE NUM =", stage_num
			#print "wires_driven_so_far",wires_driven_so_far
		#raw_input("Enter for enxt stage...")

		rv.per_stage_texts[stage_num] = []
		submodule_level = 0
		# DO WHILE LOOP FOR PER COMB LOGIC SUBMODULE LEVELS
		while True: # DO
			submodule_level_text = ""
						
			if print_debug:
				print "SUBMODULE LEVEL",submodule_level, "abs",abs_submodule_level
			
			
			#########################################################################################
			# THIS WHILE LOOP FOLLOWS WIRES TO SUBMODULES
			# First follow wires to submodules
			#print "wires_driven_so_far",wires_driven_so_far
			#print "wires_starting_level",wires_starting_level
			wires_to_follow=wires_starting_level[:]						
			while len(wires_to_follow)>0:
				# Sort wires to follow for easy debug of duplicates
				wires_to_follow = sorted(wires_to_follow)
				for driving_wire in wires_to_follow:
					if print_debug:
						print "driving_wire",driving_wire
					
					if not(driving_wire in wires_driven_so_far):
						wires_driven_so_far.append(driving_wire)
					
					# Loop over what this wire drives
					if driving_wire in logic.wire_drives:
						driven_wires = logic.wire_drives[driving_wire]
						if print_debug:
							print "driven_wires",driven_wires
							
						# Sort for easy debug
						driven_wires = sorted(driven_wires)
						for driven_wire in driven_wires:
							if print_debug:
								print "handling driven wire", driven_wire
								
							if not(driven_wire in logic.wire_driven_by):
								# In
								print "!!!!!!!!!!!! DANGLING LOGIC???????",driven_wire, "is not driven!?"
								continue
							#	# This is ok since this dangling logic isnt actually used?
							#	continue
							#	#print "logic.wire_driven_by"
							#	#print logic.wire_driven_by
							#	#print ""
														
							# Add driven wire to wires driven so far
							# Record driving this wire, at this logic level offset
						
							if not(driven_wire in wires_driven_so_far):
								wires_driven_so_far.append(driven_wire)
								
								# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ LLS  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
								if has_lls and is_zero_clk:
									lls_offset_when_driven[driven_wire] = lls_offset_when_driven[driving_wire]
								#~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~		
							
							# If the driving wire is a submodule output AND LATENCY>0 then RHS uses register style 
							# as way to ensure correct multi clock behavior	
							RHS = VHDL.GET_RHS(driving_wire, logic, parser_state, TimingParamsLookupTable, timing_params, rv.stage_ordered_submodule_list, stage_num)												

							# Submodule or not LHS is write pipe wire
							LHS = VHDL.GET_LHS(driven_wire, logic, parser_state)							
							
							# Submodules input ports is handled at hierarchy cross later - do nothing here
							if C_TO_LOGIC.WIRE_IS_SUBMODULE_PORT(driven_wire, logic):
								pass
							else:
								# Record reaching another wire
								next_wires_to_follow.append(driven_wire)	
							
							
							# Need VHDL conversions for this type assignment?
							TYPE_RESOLVED_RHS = VHDL.TYPE_RESOLVE_ASSIGNMENT_RHS(RHS, logic, driving_wire, driven_wire, parser_state)
							submodule_level_text += "	" + "	" + "	" + LHS + " := " + TYPE_RESOLVED_RHS + ";" + " -- " + driven_wire + " <= " + driving_wire + "\n"
								
								
				wires_to_follow=next_wires_to_follow[:]
				next_wires_to_follow=[]
			
			#print "followed wires to these submodules",submodule_names
			#print "now wires_driven_so_far",wires_driven_so_far
			#########################################################################################
			
			
			
			# \/\/\/\/\/\/\/\/\/\/\/\/\/\/\/ GET FULLY DRIVEN SUBMODULES TO POPULATE SUBMODULE LEVEL \/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/ 
			# Have followed this levels wires to submodules
			# We only want to consider the submodules whose inputs have all 
			# been driven by now in clock cycle to keep execution order
			# AND
			# THE HIERARCHY BOUNDARY IS BEING CROSSED AT THE RIGHT TIME
			fully_driven_submodule_inst_name_this_level_2_logic = dict()
			# Get submodule logics
			# Loop over each sumodule and check if all inputs are driven
			for submodule_inst_name in logic.submodule_instances:
				
				# Skip submodules weve done already
				already_fully_driven = submodule_inst_name in fully_driven_submodule_inst_name_2_logic
				
				#if print_debug:
				#	print ""
				#	print "########"
				#	print "SUBMODULE INST",submodule_inst_name, "FULLY DRIVEN?:",already_fully_driven
				
				if not already_fully_driven:
					#print submodule_inst_name_2_logic
					submodule_logic = LogicInstLookupTable[submodule_inst_name]
					# Check each input
					submodule_has_all_inputs_driven = True
					submodule_input_port_driving_wires = []
					for input_port_name in submodule_logic.inputs:
						driving_wire = C_TO_LOGIC.GET_SUBMODULE_INPUT_PORT_DRIVING_WIRE(logic, submodule_inst_name,input_port_name)
						submodule_input_port_driving_wires.append(driving_wire)
						if not(driving_wire in wires_driven_so_far):
							submodule_has_all_inputs_driven = False
							if print_debug:
								print "!! " + submodule_inst_name + " input wire " + input_port_name
								print " is driven by", driving_wire
								print "  <<<<<<<<<<<<< ", driving_wire , "is not (fully?) driven?"
								print " <<<<<<<<<<<<< YOU ARE PROBABALY NOT DRIVING ALL LOCAL VARIABLES COMPLETELY(STRUCTS) >>>>>>>>>>>> "
								C_TO_LOGIC.PRINT_DRIVER_WIRE_TRACE(driving_wire, logic, wires_driven_so_far)
							break
					
					
					# If all inputs are driven
					if submodule_has_all_inputs_driven: 
						fully_driven_submodule_inst_name_2_logic[submodule_inst_name]=submodule_logic
						fully_driven_submodule_inst_name_this_level_2_logic[submodule_inst_name] = submodule_logic
						if print_debug:
							print "submodule",submodule_inst_name, "HAS ALL INPUTS DRIVEN"


						# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ LLS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
						if has_lls and is_zero_clk:
							# Record LLs offset as max of driving wires
							# Do submodule_start_offset INPUT OFFSET as max of input wires
							input_port_ll_offsets = []
							for submodule_input_port_driving_wire in submodule_input_port_driving_wires:
								ll_offset = lls_offset_when_driven[submodule_input_port_driving_wire]
								input_port_ll_offsets.append(ll_offset)
							max_input_port_ll_offset = max(input_port_ll_offsets)
							
							# All submodules should be driven at some ll offset right?
							#print "wires_driven_so_far",wires_driven_so_far
							#print "lls_offset_when_driven",lls_offset_when_driven
							
							if not(submodule_inst_name in rv.submodule_start_offset):
								rv.submodule_start_offset[submodule_inst_name] = dict()
							#rv.submodule_start_offset[submodule_inst_name][stage_num] = lls_offset_when_driven[submodule_logic.inputs[0]]
							rv.submodule_start_offset[submodule_inst_name][stage_num] = max_input_port_ll_offset
							
							#DO PER STAGE (includes OUTPUT)
							# Do per stage LLs starting at the input offset
							# This lls_offset_when_driven value wont be used 
							# until the stage when the output wire is read
							# So needs lls expected in last stage
							submodule_timing_params = TimingParamsLookupTable[submodule_inst_name]
							submodule_total_lls = LogicInstLookupTable[submodule_inst_name].total_logic_levels
							if submodule_total_lls is None:
								print "What cant do this with logic levels!"
								sys.exit(0)
							#print "submodule_inst_name",submodule_inst_name
							#print "submodule_total_lls",submodule_total_lls
							submodule_latency = submodule_timing_params.GET_TOTAL_LATENCY(parser_state,TimingParamsLookupTable)
							
							
							# Get lls per stage from timing params
							if submodule_timing_params.GET_TOTAL_LATENCY(parser_state,TimingParamsLookupTable) > 0:
								print "What non zero latency for pipeline map? FIX MEhow?"
								#HOW TO ALLOCATE LLS?
								print 0/0
								sys.exit(0)
							# End offset is start offset plus lls in stage
							# Ex. 0 LLs in LL offset 0 
							# Starts and ends in stage 0
							# Ex. 1 LLs in LL offset 0 
							# ALSO STARTS AND ENDS IN STAGE 0
							if submodule_total_lls > 0:
								abs_lls_end_offset = submodule_total_lls + rv.submodule_start_offset[submodule_inst_name][stage_num] - 1
							else:
								abs_lls_end_offset = rv.submodule_start_offset[submodule_inst_name][stage_num]
								
							if not(submodule_inst_name in rv.submodule_end_offset):
								rv.submodule_end_offset[submodule_inst_name] = dict()				
							rv.submodule_end_offset[submodule_inst_name][stage_num] = abs_lls_end_offset
								
							# Do PARALLEL submodules map with start and end offsets form each stage
							# Dont do for 0 LL submodules, ok fine
							if submodule_total_lls > 0:
								# Submodule insts
								if not(stage_num in rv.stage_per_ll_submodules_map):
									rv.stage_per_ll_submodules_map[stage_num] = dict()
								# Submodule level
								if not(stage_num in rv.stage_per_ll_submodule_level_map):
									rv.stage_per_ll_submodule_level_map[stage_num] = dict()
									
								start_offset = rv.submodule_start_offset[submodule_inst_name][stage_num]
								end_offset = rv.submodule_end_offset[submodule_inst_name][stage_num]
								for abs_ll in range(start_offset,end_offset+1):
									if abs_ll < 0:
										print "<0 LL offset?"
										print start_offset,end_offset
										print lls_offset_when_driven
										sys.exit(0)
									# Submodule isnts
									if not(abs_ll in rv.stage_per_ll_submodules_map[stage_num]):
										rv.stage_per_ll_submodules_map[stage_num][abs_ll] = []
									if not(submodule_inst_name in rv.stage_per_ll_submodules_map[stage_num][abs_ll]):
										rv.stage_per_ll_submodules_map[stage_num][abs_ll].append(submodule_inst_name)
									# Submodule level
									if not(abs_ll in rv.stage_per_ll_submodule_level_map[stage_num]):
										rv.stage_per_ll_submodule_level_map[stage_num][abs_ll] = []
									rv.stage_per_ll_submodule_level_map[stage_num][abs_ll] = submodule_level
						# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
						
						
					else:
						# Otherwise save for later
						if print_debug:
							print "submodule",submodule_inst_name, "does not have all inputs driven yet"
						#next_submodule_names.append(submodule_inst_name)

			#print "got input driven submodules, wires_driven_so_far",wires_driven_so_far
			if print_debug:
				print "fully_driven_submodule_inst_name_this_level_2_logic",fully_driven_submodule_inst_name_this_level_2_logic
			#/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\
			
			
			
			############################# GET OUTPUT WIRES FROM SUBMODULE LEVEL #############################################
			# Get list of output wires for this all the submodules in this level
			# by writing submodule connections / procedure calls
			submodule_level_output_wires = []
			for submodule_inst_name in fully_driven_submodule_inst_name_this_level_2_logic:
				submodule_logic = LogicInstLookupTable[submodule_inst_name]
				
				# Record this submodule in this stage
				if stage_num not in rv.stage_ordered_submodule_list:
					rv.stage_ordered_submodule_list[stage_num] = []
				if submodule_inst_name not in rv.stage_ordered_submodule_list[stage_num]:
					rv.stage_ordered_submodule_list[stage_num].append(submodule_inst_name)
					
				# Get latency
				submodule_latency_from_container_logic = timing_params.GET_SUBMODULE_LATENCY(submodule_inst_name, parser_state, TimingParamsLookupTable)
				
				# Use submodule logic to write vhdl
				# VHDL TEXT insert?
				if submodule_logic.func_name.startswith(VHDL_INSERT.HDL_INSERT):
					hdl_insert_text = VHDL_INSERT.GET_HDL_INSERT_TEXT(submodule_logic,submodule_inst_name, logic, timing_params)			
					submodule_level_text += "	" + "	" + "	" + hdl_insert_text + "\n"
				else:
					# REGULAR PROCEDURE
					#print "submodule_inst_name",submodule_inst_name,submodule_latency_from_container_logic
					submodule_level_text += "	" + "	" + "	-- " + C_TO_LOGIC.LEAF_NAME(submodule_inst_name, True) + " LATENCY=" + str(submodule_latency_from_container_logic) +  "\n"
					procedure_call_text = VHDL.GET_PROCEDURE_CALL_TEXT(submodule_logic,submodule_inst_name, logic, timing_params, parser_state)			
					submodule_level_text += "	" + "	" + "	" + procedure_call_text + "\n"

				# Add output wires of this submodule to wires driven so far after latency
				for output_wire in submodule_logic.outputs:
					# Construct the name of this wire in the original logic
					submodule_output_wire = output_wire # submodule_inst_name + C_TO_LOGIC.SUBMODULE_MARKER + output_wire
					if print_debug:
						print "following output",output_wire
					# Add this output port wire on the submodule after the latency of the submodule
					#print "submodule_latencies[",submodule_inst_name, "]==", submodule_latency
					if print_debug:
						print "submodule_output_wire",submodule_output_wire
					wire_to_remaining_clks_before_driven[submodule_output_wire] = submodule_latency_from_container_logic
					#print "wire_to_remaining_clks_before_driven[",submodule_output_wire, "]==", wire_to_remaining_clks_before_driven[submodule_output_wire]
			
					# Set lls_offset_when_driven for this output wire
					# Get lls per stage from timing params
					submodule_timing_params = TimingParamsLookupTable[submodule_inst_name]
					submodule_latency = submodule_timing_params.GET_TOTAL_LATENCY(parser_state,TimingParamsLookupTable)
					if submodule_latency != submodule_latency_from_container_logic:
						print "submodule_latency != submodule_latency_from_container_logic"
						sys.exit(0)
						
					# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ LLS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
					if has_lls and is_zero_clk:	
						# Assume zero latency fix me
						if submodule_latency != 0:
							print "non zero submodule latency? fix me"
							sys.exit(0)
						# Set LLs offset for this wire
						submodule_total_lls = LogicInstLookupTable[submodule_inst_name].total_logic_levels
						abs_lls_offset = rv.submodule_end_offset[submodule_inst_name][stage_num]
						lls_offset_when_driven[output_wire] = abs_lls_offset
						# ONly +1 if lls > 0
						if submodule_total_lls > 0:
							lls_offset_when_driven[output_wire] += 1 # "+1" Was seeing stacked parallel submodules where they dont exist
					# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
			####################################################################################################################
		
			
			
			
			# END
			# SUBMODULE
			# LEVEL
			# Reset submodule names and wires for next submodule level
			#submodule_names = next_submodule_names
			#next_submodule_names = []
			# Wires starting next level are wires whose latency has elapsed just now
			# Also are added to wires driven so far
			wires_starting_level=[]
			for wire in wire_to_remaining_clks_before_driven:
				if wire_to_remaining_clks_before_driven[wire]==0:
					if not(wire in wires_driven_so_far):
						if print_debug:
							print "wire remaining clks done, is driven now",wire
						wires_driven_so_far.append(wire)
						wires_starting_level.append(wire)
			
			
			# Is this the last submodule level per timing params?
			is_last_abs_submodule_level = False
			last_abs_submodule_level = None
			if stage_num in timing_params.stage_to_last_abs_submodule_level:
				last_abs_submodule_level = timing_params.stage_to_last_abs_submodule_level[stage_num]
				is_last_abs_submodule_level = last_abs_submodule_level== abs_submodule_level
			
			
			# This ends one submodule level
			submodule_level_prepend_text = "	" + "	" + "	" + "-- SUBMODULE LEVEL " + str(submodule_level) + "(abs " + str(abs_submodule_level) + ")" + "\n"
			if submodule_level_text != "":
				rv.per_stage_texts[stage_num].append(submodule_level_prepend_text + submodule_level_text)
				submodule_level = submodule_level + 1
				abs_submodule_level = abs_submodule_level + 1
			
			
			# WHILE CHECK
			#if not( len(wires_starting_level)>0 or len(submodule_names)>0  ):
			if not( len(wires_starting_level)>0 ) or is_last_abs_submodule_level:
				break;
			else:
				# Record these last wires starting level and loop again
				if last_wires_starting_level == wires_starting_level:
					print "Same wires starting level?"
					print wires_starting_level
					if print_debug:
						sys.exit(0)
					print_debug = True
					#sys.exit(0)
				else:				
					last_wires_starting_level = wires_starting_level[:]
			
		# PER CLOCK
		# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ LLS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
		if has_lls and is_zero_clk:
			# GEt max lls this stage
			if stage_num not in rv.stage_per_ll_submodules_map:
				print "It looks like you are describing zero levels of logic?"
				sys.exit(0)
			rv.max_lls_per_stage[stage_num] = max(rv.stage_per_ll_submodules_map[stage_num].keys()) +1 # +1 since 0 indexed
		# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
		# TODO? reset ll offsets?

		# PER CLOCK decrement latencies
		stage_num = stage_num +1
		for wire in wire_to_remaining_clks_before_driven:
			#if wire_to_remaining_clks_before_driven[wire] >= 0:
			wire_to_remaining_clks_before_driven[wire]=wire_to_remaining_clks_before_driven[wire]-1
	
	rv.num_stages = stage_num
	
	
	
	# Write cache
	WRITE_PIPELINE_MAP_CACHE_FILE(logic, rv, TimingParamsLookupTable, parser_state)
	return rv

#return submodule_level_markers, ending_submodule_levels
def GET_SUBMODULE_LEVEL_MARKERS(zero_clk_pipeline_map):
	
	total_lls = zero_clk_pipeline_map.max_lls_per_stage[0]
	
	submodule_level_markers = []
	ending_submodule_levels = []
	# Get ll offsets where submodule level changes
	last_sl = zero_clk_pipeline_map.stage_per_ll_submodule_level_map[0][0]
	curr_sl = zero_clk_pipeline_map.stage_per_ll_submodule_level_map[0][0]
	for ll_i in range(0, max(zero_clk_pipeline_map.stage_per_ll_submodule_level_map[0].keys()) +1):
		curr_sl = zero_clk_pipeline_map.stage_per_ll_submodule_level_map[0][ll_i]		
		if curr_sl != last_sl:
			# Found diff, assume is at x.0? as opposed to x.99999
			marker_ll = ll_i
			marker_slice_pos = float(marker_ll) / float(total_lls)
			submodule_level_markers.append(marker_slice_pos)
			ending_submodule_levels.append(last_sl)
		last_sl = curr_sl
			
	return submodule_level_markers,ending_submodule_levels

def GET_SHELL_CMD_OUTPUT(cmd_str):
	return subprocess.Popen(cmd_str, shell=True, stdout=subprocess.PIPE).stdout.read()
	
def BUILD_HASH_EXT(Logic, TimingParamsLookupTable, parser_state):
	LogicInstLookupTable = parser_state.LogicInstLookupTable
	# Need unique ID for this configuration of submodules, regular submodules use latency as keey
	submodule_names_and_latencies = []
	
	# Top level starts with just inst name and RAW HDL slices (if applicable)
	top_level_str = Logic.inst_name + "_"
	if len(Logic.submodule_instances) <= 0:
		timing_params = TimingParamsLookupTable[Logic.inst_name]
		top_level_str += str(timing_params.slices) + "_"
	submodule_names_and_latencies.append(top_level_str)
	
	# Fake recurses through every submodule instance
	next_logics = []
	for submodule_inst in Logic.submodule_instances:
		next_logics.append(LogicInstLookupTable[submodule_inst])

	while len(next_logics) > 0:
		new_next_logics = []
		for next_logic in next_logics:
			next_logic_timing_params = TimingParamsLookupTable[next_logic.inst_name]
			
			# Write latency key for this logic
			# If no submodules then is raw HDl and use slices as latency key
			if len(next_logic.submodule_instances) <= 0:
				latency_key = str(next_logic_timing_params.slices)
				submodule_names_and_latencies.append( next_logic.inst_name + "_" + latency_key + "_" )
			else:
				# Has submodules
				# Latency key is just integer				
				latency_key = str(next_logic_timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable))
				submodule_names_and_latencies.append( next_logic.inst_name + "_" + latency_key + "_" )
				
			# Repeat this for each submodule			
			for submodule_inst in next_logic.submodule_instances:
				submodule_logic = LogicInstLookupTable[submodule_inst]
				if submodule_logic not in new_next_logics:
					new_next_logics.append(submodule_logic)						
						
		next_logics = new_next_logics
		
	# Sort list of names and latencies
	unsorted_submodule_names_and_latencies = submodule_names_and_latencies[:]
	submodule_names_and_latencies = sorted(unsorted_submodule_names_and_latencies)
	
	s = ""
	for submodule_name_and_latency in submodule_names_and_latencies:
		s += submodule_name_and_latency
		
	hash_ext = "_" + ((hashlib.md5(s).hexdigest())[0:4]) #4 chars enough?
	
		
	return hash_ext
	
# Returns updated TimingParamsLookupTable
# None if sliced through globals
def SLICE_DOWN_HIERARCHY_WRITE_VHDL_PACKAGES(logic, new_slice_pos, parser_state, TimingParamsLookupTable, write_files=True):	
	# Get timing params for this logic
	timing_params = TimingParamsLookupTable[logic.inst_name]
	# Add slice
	timing_params.ADD_SLICE(new_slice_pos)
	slice_index = timing_params.slices.index(new_slice_pos)
	slice_ends_stage = slice_index
	# Write into timing params dict
	TimingParamsLookupTable[logic.inst_name] = timing_params
	
	# Raw HDL doesnt need further slicing, bottom of hierarchy
	if len(logic.submodule_instances) > 0:
		# Get the zero clock pipeline map for this logic
		zero_clk_pipeline_map = GET_ZERO_CLK_PIPELINE_MAP(logic, parser_state)
		total_lls = zero_clk_pipeline_map.max_lls_per_stage[0]
		epsilon = SLICE_EPSILON(total_lls)
		
		# Check if slice is through submodule marker
		slice_through_submodule_level_marker = False
		submodule_level_markers, ending_submodule_levels = GET_SUBMODULE_LEVEL_MARKERS(zero_clk_pipeline_map)
		for i in range(0, len(submodule_level_markers)):
			submodule_level_marker = submodule_level_markers[i]
			ending_submodule_level = ending_submodule_levels[i]
			if SLICE_POS_EQ(new_slice_pos, submodule_level_marker , epsilon):
				# Double check not slicing twice on this level
				if ending_submodule_level in timing_params.stage_to_last_abs_submodule_level.values():
					return None
				
				# Indicate this submodule ends a stage
				slice_through_submodule_level_marker = True
				timing_params.stage_to_last_abs_submodule_level[slice_ends_stage]=ending_submodule_level
				#print "stage_per_ll_submodule_level_map",zero_clk_pipeline_map.stage_per_ll_submodule_level_map
				#print "logic.inst_name",logic.inst_name
				#print "submodule levels:",submodule_level_markers
				#print "slice:",new_slice_pos
				#print "ends stage:",slice_ends_stage
				#print "=="
				
		# Write into timing params dict
		TimingParamsLookupTable[logic.inst_name] = timing_params

		
		#IF SLICE WAS THROUGH SUBMODULE LEVEL MARKER THEN CAN'T BE THROUGH SUBMODULE
		if not slice_through_submodule_level_marker:
			# Handle submodules now
			# DOESNT MATTER raw hdl or now just apply local slices using SLICE_DOWN_HIERARCHY
			# Slice submodule levels and down hierarchy into submodules
				
			# Get the LL offset as float
			lls_offset_float = new_slice_pos * total_lls
			#print "LLs Offset (float):", lls_offset_float
			lls_offset = math.floor(lls_offset_float)
			#print "LLs Offset (int):", lls_offset
			lls_offset_decimal = lls_offset_float - lls_offset
			#print "LLs Offset Decimal Part:", lls_offset_decimal
			
			
			# Get submodules at this offset
			submodule_insts = zero_clk_pipeline_map.stage_per_ll_submodules_map[0][lls_offset]
			# Given the known start offset what is the local offset for each submodule?
			# Slice each submodule at that offset
			for submodule_inst in submodule_insts:
				# Only slice when >= 1 LL and not in a global function
				submodule_logic = parser_state.LogicInstLookupTable[submodule_inst]
				#print "submodule_inst",submodule_inst
				#print "submodule_logic.containing_inst",submodule_logic.containing_inst
				containing_logic = parser_state.LogicInstLookupTable[submodule_logic.containing_inst]
				#print "submodule_inst",submodule_inst
				#print "submodule_logic.containing_inst",submodule_logic.containing_inst
				#print "containing_logic.uses_globals",containing_logic.uses_globals
				if submodule_logic.total_logic_levels <= 0:
					continue

				submodule_timing_params = TimingParamsLookupTable[submodule_logic.inst_name]
				start_offset = zero_clk_pipeline_map.submodule_start_offset[submodule_inst][0]	
				local_offset = lls_offset - start_offset
				local_offset_w_decimal = local_offset + lls_offset_decimal
				
				#print "start_offset",start_offset
				#print "local_offset",local_offset
				#print "local_offset_w_decimal",local_offset_w_decimal
				
				# Convert to percent to add slice
				submodule_total_lls = submodule_logic.total_logic_levels
				slice_pos = float(local_offset_w_decimal) / float(submodule_total_lls)			
				#print "	Slicing:", submodule_inst
				#print "		@", slice_pos

				if containing_logic.uses_globals:
					# Can't slice globals, return index of bad slice
					return slice_index
						
				# Slice into that submodule
				TimingParamsLookupTable = SLICE_DOWN_HIERARCHY_WRITE_VHDL_PACKAGES(submodule_logic, slice_pos, parser_state, TimingParamsLookupTable, write_files)	
				
				# Might be bas slice
				if type(TimingParamsLookupTable) is not dict:
					return TimingParamsLookupTable
				
	
	if write_files:	
		# Final write package
		# Write VHDL file for submodule
		# Re write submodule package with updated timing params
		timing_params = TimingParamsLookupTable[logic.inst_name]
		syn_out_dir = GET_OUTPUT_DIRECTORY(logic, implement=False)
		if not os.path.exists(syn_out_dir):
			os.makedirs(syn_out_dir)		
		VHDL.GENERATE_PACKAGE_FILE(logic, parser_state, TimingParamsLookupTable, timing_params, syn_out_dir)
	
	
	#print logic.inst_name, "len(timing_params.slices)", len(timing_params.slices)
	
	return TimingParamsLookupTable
	
	
# Returns index of bad slices
def GET_TIMING_PARAMS_AND_WRITE_VHDL_PACKAGES(logic, current_slices, parser_state, write_files=True):	
	# Reset to initial timing params before adding slices
	TimingParamsLookupTable = dict()
	for logic_inst_name in parser_state.LogicInstLookupTable: 
		logic_i = parser_state.LogicInstLookupTable[logic_inst_name]
		timing_params_i = TimingParams(logic_i)
		TimingParamsLookupTable[logic_inst_name] = timing_params_i
	
	# Do slice to main logic for each slice
	for current_slice_i in current_slices:
		TimingParamsLookupTable = SLICE_DOWN_HIERARCHY_WRITE_VHDL_PACKAGES(logic, current_slice_i, parser_state, TimingParamsLookupTable, write_files)
		# Might be bad slice
		if type(TimingParamsLookupTable) is not dict:
			return TimingParamsLookupTable
	
	est_total_latency = len(current_slices)
	timing_params = TimingParamsLookupTable[logic.inst_name]
	total_latency = timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
	if est_total_latency != total_latency:
		print "Did not slice down hierarchy right!? est_total_latency",est_total_latency, "calculated total_latency",total_latency
		print "slices:",current_slices
		print "timing_params.stage_to_last_abs_submodule_level",timing_params.stage_to_last_abs_submodule_level
		for logic_inst_name in parser_state.LogicInstLookupTable: 
			logic_i = parser_state.LogicInstLookupTable[logic_inst_name]
			timing_params_i = TimingParams(logic_i)
			print "logic_i.inst_name",logic_i.inst_name, timing_params_i.slices
		
		sys.exit(0)
	
	return TimingParamsLookupTable
				
				

def DO_SYN_WITH_SLICES(current_slices, Logic, zero_clk_pipeline_map, parser_state, do_latency_check=True, do_get_stage_range=True):
	clock_mhz = INF_MHZ
	implement = False
	total_latency = len(current_slices)
	
	# Dont write files if log file exists
	write_files = False
	log_file_exists = False
	output_directory = GET_OUTPUT_DIRECTORY(Logic, implement=False)
	TimingParamsLookupTable = GET_TIMING_PARAMS_AND_WRITE_VHDL_PACKAGES(Logic, current_slices, parser_state, write_files)
	
	# TimingParamsLookupTable == None 
	# means these slices go through global code
	if type(TimingParamsLookupTable) is not dict:
		print "Can't syn when slicing through globals!"
		return None, None, None, None
		#stage_range, timing_report, total_latency, TimingParamsLookupTable	
	
	# Check if log file exists
	timing_params = TimingParamsLookupTable[Logic.inst_name]
	hash_ext = timing_params.GET_HASH_EXT(TimingParamsLookupTable, parser_state)		
	log_path = output_directory + "/vivado" + "_" +  str(total_latency) + "CLK_" + str(clock_mhz) + "MHz" + hash_ext + ".log"
	log_file_exists = os.path.exists(log_path)
	if not log_file_exists:
		#print "Slicing the RAW HDL submodules..."
		write_files = True
		TimingParamsLookupTable = GET_TIMING_PARAMS_AND_WRITE_VHDL_PACKAGES(Logic, current_slices, parser_state, write_files)
		
	# Then get main logic timing params
	#print "Recalculating total latency (building pipeline map)..."
	total_latency = len(current_slices)
	timing_params = TimingParamsLookupTable[Logic.inst_name]
	if do_latency_check:
		total_latency = timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
		#print "Latency (clocks):",total_latency
		if len(current_slices) != total_latency:
			print "Calculated total latency based on timing params does not match latency from number of slices?"
			print "	current_slices:",current_slices
			print "	total_latency",total_latency
			sys.exit(0)
	
	# Then run syn
	timing_report = VIVADO.SYN_IMP_AND_REPORT_TIMING(Logic, parser_state, TimingParamsLookupTable, implement, clock_mhz, total_latency, hash_ext)
	if timing_report.start_reg_name is None:
		print timing_report.orig_text
		print "Using a bad syn log file?"
		sys.exit(0)
	
	stage_range = None
	if do_get_stage_range:
		stage_range = timing_params.GET_ABS_STAGE_RANGE_FROM_TIMING_REPORT(timing_report, parser_state, TimingParamsLookupTable)
		
	return stage_range, timing_report, total_latency, TimingParamsLookupTable
	

class SweepState:
	def __init__(self, zero_clk_pipeline_map): #, zero_clk_pipeline_map):
		# Slices by default is empty
		self.current_slices = []
		self.seen_slices=[] # list of above lists
		self.mhz_to_latency = dict()
		self.mhz_to_slices = dict()
		self.stage_range = [0] # The current worst path stage estimate from the timing report
		self.working_stage_range = [0] # Temporary stage range to guide adjustments to slices
		self.timing_report = None # Current timing report
		self.total_latency = 0 # Current total latency
		self.latency_2_best_slices = dict() 
		self.latency_2_best_delay = dict()
		self.zero_clk_pipeline_map = copy.copy(zero_clk_pipeline_map)
		self.slice_step = 0.0
		self.stages_adjusted_this_latency = {0 : 0} # stage -> num times adjusted
		self.TimingParamsLookupTable = None # Current timing params with current slices
		
		
def GET_MOST_RECENT_OR_DEFAULT_SWEEP_STATE(Logic, zero_clk_pipeline_map):	
	# Try to use cache
	cached_sweep_state = GET_MOST_RECENT_CACHED_SWEEP_STATE(Logic)
	sweep_state = None
	if not(cached_sweep_state is None):
		print "Using cached most recent sweep state..."
		sweep_state = cached_sweep_state
	else:
		print "Starting with blank sweep state..."
		# Looks LLs per stage
		sweep_state = SweepState(zero_clk_pipeline_map)
	
	return sweep_state
	
def GET_MOST_RECENT_CACHED_SWEEP_STATE(Logic):
	output_directory = GET_OUTPUT_DIRECTORY(Logic, implement=False)
	# Search for most recently modified
	sweep_files = [file for file in glob.glob(os.path.join(output_directory, '*.sweep'))]
	
	if len(sweep_files) > 0 :
		#print "sweep_files",sweep_files
		sweep_files.sort(key=os.path.getmtime)
		print sweep_files[-1] # most recent file
		most_recent_sweep_file = sweep_files[-1]
		
		with open(most_recent_sweep_file, 'rb') as input:
			sweep_state = pickle.load(input)
			return sweep_state
	else:
		return None
	
	
		
def WRITE_SWEEP_STATE_CACHE_FILE(state, Logic):#, LogicInstLookupTable):
	#TimingParamsLookupTable = state.TimingParamsLookupTable
	output_directory = GET_OUTPUT_DIRECTORY(Logic, implement=False)
	# ONly write one sweep per clk for space sake?
	filename = Logic.inst_name.replace("/","_") + "_" + str(state.total_latency) + "CLK.sweep"
	filepath = output_directory + "/" + filename
	
	# Write dir first if needed
	if not os.path.exists(output_directory):
		os.makedirs(output_directory)
		
	# Write file
	with open(filepath, 'w') as output:
		pickle.dump(state, output, pickle.HIGHEST_PROTOCOL)

def SHIFT_SLICE(slices, index, direction, amount,min_dist):
	if amount == 0:
		return slices
	
	curr_slice = slices[index]
	
	# New slices dont incldue the current one until adjsuted
	new_slices = slices[:]
	new_slices.remove(curr_slice)
	new_slice = None
	if direction == 'r':
		new_slice = curr_slice + amount
						
	elif direction == 'l':
		new_slice = curr_slice - amount
		
	else:
		print "WHATATSTDTSTTTS"
		sys.exit(0)
		
		
	if new_slice >= 1.0:
		new_slice = 1.0 - min_dist
	if new_slice < 0.0:
		new_slice = min_dist	
		
		
	# if new slice is within min_dist of another slice back off change
	for slice_i in new_slices:
		if SLICE_POS_EQ(slice_i, new_slice, min_dist):			
			if direction == 'r':
				new_slice = slice_i - min_dist # slightly to left of matching slice 
								
			elif direction == 'l':
				new_slice = slice_i + min_dist # slightly to right of matching slice
				
			else:
				print "WHATATSTD$$$$TSTTTS"
				sys.exit(0)
			break
	
	
	new_slices.append(new_slice)
	new_slices = sorted(new_slices)
	
	return new_slices
		

	
def GET_BEST_GUESS_IDEAL_SLICES(state):
	# Build ideal slices at this latency
	chunks = state.total_latency+1
	slice_per_chunk = 1.0/chunks
	slice_total = 0
	ideal_slices = []
	for i in range(0,state.total_latency):
		slice_total += slice_per_chunk
		ideal_slices.append(slice_total)
		
	return ideal_slices
		

def REDUCE_SLICE_STEP(slice_step, total_latency, epsilon):
	n=0
	while True:
		maybe_new_slice_step = 1.0/((SLICE_STEPS_BETWEEN_REGS+1+n)*(total_latency+1))
		if maybe_new_slice_step < epsilon/2.0: # Allow half as small>>>???
			maybe_new_slice_step = epsilon/2.0
			return maybe_new_slice_step
			
		if maybe_new_slice_step < slice_step:
			return maybe_new_slice_step
		
		n = n + 1
		
def INCREASE_SLICE_STEP(slice_step, state):
	n=99999 # Start from small and go up via lower n
	while True:
		maybe_new_slice_step = 1.0/((SLICE_STEPS_BETWEEN_REGS+1+n)*(state.total_latency+1))
		if maybe_new_slice_step > slice_step:
			return maybe_new_slice_step
		
		n = n - 1
	
	
def ROUND_SLICES_AWAY_FROM_GLOBAL_LOGIC(logic, current_slices, slice_step, parser_state, zero_clk_pipeline_map):
	#zero_clk_pipeline_map = GET_ZERO_CLK_PIPELINE_MAP(logic, parser_state)
	total_lls = zero_clk_pipeline_map.max_lls_per_stage[0]
	epsilon = SLICE_EPSILON(total_lls)
	slice_step = epsilon / 2.0
	working_slices = current_slices[:]
	working_params_dict = None
	seen_bad_slices = []
	min_dist = SLICE_DISTANCE_MIN(total_lls)
	while working_params_dict is None:
		# Try to find next bad slice
		bad_slice_or_params = GET_TIMING_PARAMS_AND_WRITE_VHDL_PACKAGES(logic, working_slices, parser_state, write_files=False)
	
		if type(bad_slice_or_params) is dict:
			# Got timing params dict so good
			working_params_dict = bad_slice_or_params
			break
		else:
			# Got bad slice
			bad_slice = bad_slice_or_params
			
			# Done too much?
			# If and slice index has been adjusted twice something is wrong and give up
			for slice_index in range(0,len(current_slices)):
				if seen_bad_slices.count(slice_index) >= 2:
					return None
			seen_bad_slices.append(bad_slice)
			
			# Get initial slice value
			to_left_val = working_slices[bad_slice]
			to_right_val = working_slices[bad_slice]
			
			# Once go too far we try again with lower slice step
			while (to_left_val > slice_step) or (to_right_val < 1.0-slice_step):
				## Push slices to left and right until get working slices
				#pushed_left_slices = SHIFT_SLICE(working_slices, bad_slice, 'l', slice_step, min_dist)
				#pushed_right_slices = SHIFT_SLICE(working_slices, bad_slice, 'r', slice_step, min_dist)
				if (to_left_val > slice_step):
					to_left_val = to_left_val - slice_step
				if (to_right_val < 1.0-slice_step):
					to_right_val = to_right_val + slice_step
				pushed_left_slices = working_slices[:]
				pushed_left_slices[bad_slice] = to_left_val
				pushed_right_slices = working_slices[:]
				pushed_right_slices[bad_slice] = to_right_val
				
				# Sanity sort them? idk wtf
				pushed_left_slices = sorted(pushed_left_slices)
				pushed_right_slices = sorted(pushed_right_slices)
				
				#print "working_slices",working_slices
				#print "total_lls",total_lls
				#print "epsilon",epsilon
				#print "min_dist",min_dist
				#print "pushed_left_slices",pushed_left_slices
				#print "pushed_right_slices",pushed_right_slices
				
				
				# Try left and right
				left_bad_slice_or_params = GET_TIMING_PARAMS_AND_WRITE_VHDL_PACKAGES(logic, pushed_left_slices, parser_state, write_files=False)
				right_bad_slice_or_params = GET_TIMING_PARAMS_AND_WRITE_VHDL_PACKAGES(logic, pushed_right_slices, parser_state, write_files=False)
			
				if (type(left_bad_slice_or_params) is type(0)) and (left_bad_slice_or_params != bad_slice):
				#if (type(left_bad_slice_or_params) is type(0)) and (str(pushed_left_slices) not in seen_slices):
					# Got different bad slice, use pushed slices as working
					#print "bad left slice", left_bad_slice_or_params
					bad_slice = left_bad_slice_or_params
					working_slices = pushed_left_slices[:]
					break
				elif (type(right_bad_slice_or_params) is type(0)) and (right_bad_slice_or_params != bad_slice):
				#elif (type(right_bad_slice_or_params) is type(0)) and (str(pushed_right_slices) not in seen_slices):
					# Got different bad slice, use pushed slices as working
					#print "bad right slice", right_bad_slice_or_params
					bad_slice = right_bad_slice_or_params
					working_slices = pushed_right_slices[:]
					break
				elif type(left_bad_slice_or_params) is dict:
					# Worked
					working_params_dict = left_bad_slice_or_params
					working_slices = pushed_left_slices[:]
					break
				elif type(right_bad_slice_or_params) is dict:
					# Worked
					working_params_dict = right_bad_slice_or_params
					working_slices = pushed_right_slices[:]
					break
				else:
					#print "WHAT"
					#print left_bad_slice_or_params, right_bad_slice_or_params
					pass
			
			
	return working_slices
	
# Returns sweep state
def ADD_ANOTHER_PIPELINE_STAGE(logic, state, parser_state):
	# Increment latency and get best guess slicing
	state.total_latency = state.total_latency + 1
	state.slice_step = 1.0/((SLICE_STEPS_BETWEEN_REGS+1)*(state.total_latency+1))
	print "Starting slice_step:",state.slice_step	
	best_guess_ideal_slices = GET_BEST_GUESS_IDEAL_SLICES(state)
	print "Best guess slices:", best_guess_ideal_slices
	# Adjust to not slice globals
	print "Adjusting to not slice through global logic..."
	rounded_slices = ROUND_SLICES_AWAY_FROM_GLOBAL_LOGIC(logic, best_guess_ideal_slices, state.slice_step, parser_state, state.zero_clk_pipeline_map)
	if rounded_slices is None:
		print "Could not round slices away from globals? Can't add another pipeline stage!"
		sys.exit(0)	
	print "Starting this latency with: ", rounded_slices
	state.current_slices = rounded_slices[:]
	
	
	# Reset adjustments
	for stage in range(0, len(state.current_slices) + 1):
		state.stages_adjusted_this_latency[stage] = 0

	return state
		
		
def GET_DEFAULT_SLICE_ADJUSTMENTS(logic, slices, stage_range, parser_state, zero_clk_pipeline_map, slice_step, epsilon, min_dist, multiplier=1, include_bad_changes=True):
	if len(slices) == 0:
		# no possible adjsutmwents
		# Return list with no change
		return [ slices ]
		
	INCLUDE_BAD_CHANGES_TOO = include_bad_changes
	
	# At least 1 slice, get possible changes
	rv = []
	last_stage = len(slices)
	for stage in stage_range:
		if stage == 0:
			# Since there is only one solution to a stage 0 path
			# push slice as far to left as to not be seen change
			n = multiplier

			current_slices = SHIFT_SLICE(slices, 0, 'l', slice_step*n, min_dist)
			if current_slices not in rv:
				rv += [current_slices]
			
			if INCLUDE_BAD_CHANGES_TOO:
				current_slices = SHIFT_SLICE(slices, 0, 'r', slice_step*n, min_dist)
				if current_slices not in rv:
					rv += [current_slices]
						
		elif stage == last_stage:
			# Similar, only one way to fix last stage path so loop until change is unseen
			n = multiplier

			last_index = len(slices)-1
			current_slices = SHIFT_SLICE(slices, last_index, 'r', slice_step*n, min_dist)
			if current_slices not in rv:
				rv += [current_slices]
			
			if INCLUDE_BAD_CHANGES_TOO:
				current_slices = SHIFT_SLICE(slices, last_index, 'l', slice_step*n, min_dist)
				if current_slices not in rv:
					rv += [current_slices]
			
		elif stage > 0:
			# 3 possible changes
			# Right slice to left
			current_slices = SHIFT_SLICE(slices, stage, 'l', slice_step*multiplier, min_dist)
			if current_slices not in rv:
				rv += [current_slices]
			if INCLUDE_BAD_CHANGES_TOO: 
				current_slices = SHIFT_SLICE(slices, stage, 'r', slice_step*multiplier, min_dist)
				if current_slices not in rv:
					rv += [current_slices]
			
			# Left slice to right
			current_slices = SHIFT_SLICE(slices, stage-1, 'r', slice_step*multiplier, min_dist)
			if current_slices not in rv:
				rv += [current_slices]
			if INCLUDE_BAD_CHANGES_TOO:
				current_slices = SHIFT_SLICE(slices, stage-1, 'l', slice_step*multiplier, min_dist)
				if current_slices not in rv:
					rv += [current_slices]

			# BOTH
			current_slices = SHIFT_SLICE(slices, stage, 'l', slice_step*multiplier, min_dist)
			current_slices = SHIFT_SLICE(current_slices, stage-1, 'r', slice_step*multiplier, min_dist)
			if current_slices not in rv:
				rv += [current_slices]
			if INCLUDE_BAD_CHANGES_TOO:
				current_slices = SHIFT_SLICE(slices, stage, 'r', slice_step*multiplier, min_dist)
				current_slices = SHIFT_SLICE(current_slices, stage-1, 'l', slice_step*multiplier, min_dist)
				if current_slices not in rv:
					rv += [current_slices]
			
	
	print "Rounding slices:",rv
	# Round each possibility away from globals
	rounded_rv = []
	for rv_slices in rv:
		rounded = ROUND_SLICES_AWAY_FROM_GLOBAL_LOGIC(logic, rv_slices, slice_step, parser_state, zero_clk_pipeline_map)
		if rounded is not None:
			rounded_rv.append(rounded)
	
	# Remove duplicates
	rounded_rv  = REMOVE_DUP_SLICES(rounded_rv, epsilon)
			
	return rounded_rv
	
def REMOVE_DUP_SLICES(slices_list, epsilon):
	rv_slices_list = []
	for slices in slices_list:
		dup = False
		for rv_slices in rv_slices_list:
			if SLICES_EQ(slices, rv_slices, epsilon):
				dup = True
		if not dup:
			rv_slices_list.append(slices)
	return rv_slices_list
	
# Each call to SYN_IMP_AND_REPORT_TIMING is a new thread
def PARALLEL_SYN_WITH_SLICES_PICK_BEST(Logic, state, parser_state, possible_adjusted_slices):
	clock_mhz = INF_MHZ
	implement = False
	
	NUM_PROCESSES = int(open("num_processes.cfg",'r').readline())
	
	my_thread_pool = ThreadPool(processes=NUM_PROCESSES)
	
	# Stage range
	if len(possible_adjusted_slices) <= 0:
		# No adjustments no change in state
		return copy.copy(state)
	
		
	# Round slices and try again
	print "Rounding slices for syn..."
	old_possible_adjusted_slices = possible_adjusted_slices[:]
	possible_adjusted_slices = []
	for slices in old_possible_adjusted_slices:
		rounded_slices = ROUND_SLICES_AWAY_FROM_GLOBAL_LOGIC(Logic, slices, state.slice_step, parser_state, state.zero_clk_pipeline_map)
		if rounded_slices not in possible_adjusted_slices:
			possible_adjusted_slices.append(rounded_slices)
	
	print "Slices after rounding:"
	for slices in possible_adjusted_slices: 
		print slices
	
	slices_to_syn_tup = dict()
	if NUM_PROCESSES > 1:
		slices_to_thread = dict()
		for slices in possible_adjusted_slices:
			#my_async_result = my_thread.apply_async(VIVADO.SYN_IMP_AND_REPORT_TIMING, (Logic, parser_state, LogicInst2TimingParams, implement, clock_mhz))
			do_latency_check=False
			do_get_stage_range=False
			my_async_result = my_thread_pool.apply_async(DO_SYN_WITH_SLICES, (slices, Logic, state.zero_clk_pipeline_map, parser_state, do_latency_check, do_get_stage_range))
			slices_to_thread[str(slices)] = my_async_result
			
		# Collect all the results
		for slices in possible_adjusted_slices:
			my_async_result = slices_to_thread[str(slices)]
			print "...Waiting on synthesis for slices:", slices
			my_syn_tup = my_async_result.get()
			slices_to_syn_tup[str(slices)] = my_syn_tup
	else:
		for slices in possible_adjusted_slices:
			#my_async_result = my_thread.apply_async(VIVADO.SYN_IMP_AND_REPORT_TIMING, (Logic, parser_state, LogicInst2TimingParams, implement, clock_mhz))
			do_latency_check=False
			do_get_stage_range=False
			my_syn_tup = DO_SYN_WITH_SLICES(slices, Logic, state.zero_clk_pipeline_map, parser_state, do_latency_check, do_get_stage_range)
			slices_to_syn_tup[str(slices)] = my_syn_tup
		
	# Kill all vivados once done
	#GET_SHELL_CMD_OUTPUT("killall vivado")
	
	# Check that each result isnt the same (only makes sense if > 1 elements)
	syn_tups = slices_to_syn_tup.values()
	datapath_delays = []
	for syn_tup in syn_tups:
		datapath_delays.append(syn_tup[1].data_path_delay)
	all_same_delays = len(set(datapath_delays))==1 and len(datapath_delays) > 1
	
	if all_same_delays:
		# Cant pick best
		print "All adjustments yield same result..."
		return copy.copy(state)
	else:
		# Pick the best result
		best_slices = None
		best_mhz = 0
		best_syn_tup = None
		for slices in possible_adjusted_slices:
			syn_tup = slices_to_syn_tup[str(slices)]
			timing_report = syn_tup[1]
			mhz = (1.0 / (timing_report.data_path_delay / 1000.0)) 
			print "	", slices, "MHz:", mhz
			if (mhz > best_mhz) :
				best_mhz = mhz
				best_slices = slices
				best_syn_tup = syn_tup
				
		# Check for tie
		best_tied_slices = []
		for slices in possible_adjusted_slices:
			syn_tup = slices_to_syn_tup[str(slices)]
			timing_report = syn_tup[1]
			mhz = (1.0 / (timing_report.data_path_delay / 1000.0)) 
			if (mhz == best_mhz) :
				best_tied_slices.append(slices)
		
		# Pick best unseen from tie
		if len(best_tied_slices) > 1:
			# Pick first one not seen
			for best_tied_slice in best_tied_slices:
				syn_tup = slices_to_syn_tup[str(best_tied_slice)]
				timing_report = syn_tup[1]
				mhz = (1.0 / (timing_report.data_path_delay / 1000.0)) 
				if not SEEN_SLICES(best_tied_slice, state):
					best_mhz = mhz
					best_slices = best_tied_slice
					best_syn_tup = syn_tup
					break
		
		# Update state with return value from best
		rv_state = copy.copy(state)
		# Did not do stage range above, get now
		rv_state.timing_report = best_syn_tup[1]
		rv_state.TimingParamsLookupTable = best_syn_tup[3]
		best_timing_params = rv_state.TimingParamsLookupTable[Logic.inst_name]
		rv_state.total_latency = best_timing_params.GET_TOTAL_LATENCY(parser_state, rv_state.TimingParamsLookupTable)
		rv_state.stage_range = best_timing_params.GET_ABS_STAGE_RANGE_FROM_TIMING_REPORT(rv_state.timing_report, parser_state, rv_state.TimingParamsLookupTable)
		rv_state.current_slices = best_slices
	
	return rv_state
	
	
def LOG_SWEEP_STATE(state, Logic):
	# Record the new delay
	print "CURRENT SLICES:", state.current_slices
	print "Slice Step:", state.slice_step	
	logic_delay_percent = state.timing_report.logic_delay / state.timing_report.data_path_delay
	mhz = (1.0 / (state.timing_report.data_path_delay / 1000.0)) 
	print "MHz:", mhz
	print "Data path delay (ns):",state.timing_report.data_path_delay
	print "Logic delay (ns):",state.timing_report.logic_delay, "(",logic_delay_percent,"%)"
	print "Logic levels:",state.timing_report.logic_levels
	print "STAGE RANGE:",state.stage_range
	
	# After syn working stage range is from timing report
	state.working_stage_range = state.stage_range[:]
	
	# Print best so far for this latency
	print "Latency (clks):", state.total_latency
	if state.total_latency in state.latency_2_best_slices:
		ideal_slices = state.latency_2_best_slices[state.total_latency]
		best_delay = state.latency_2_best_delay[state.total_latency]
		best_mhz = 1000.0 / best_delay
		print "	Best so far: = ", best_mhz, "MHz:	",ideal_slices
	
	# Keep record of ___BEST___ delay and best slices
	if state.total_latency in state.latency_2_best_delay:
		# Is it better?
		if state.timing_report.data_path_delay < state.latency_2_best_delay[state.total_latency]:
			state.latency_2_best_delay[state.total_latency] = state.timing_report.data_path_delay
			state.latency_2_best_slices[state.total_latency] = state.current_slices[:]
	else:
		# Just add
		state.latency_2_best_delay[state.total_latency] = state.timing_report.data_path_delay
		state.latency_2_best_slices[state.total_latency] = state.current_slices[:]
	# RECORD __ALL___ MHZ TO LATENCY AND SLICES
	# Only add if there are no higher Mhz with lower latency already
	# I.e. ignore obviously bad results
	# What the fuck am I talking about?
	do_add = True
	for mhz_i in sorted(state.mhz_to_latency):
		latency_i = state.mhz_to_latency[mhz_i]
		if (mhz_i > mhz) and (latency_i < state.total_latency):
			# Found a better result already - dont add this high latency result
			print "Have better result so far (", mhz_i, "Mhz",latency_i, "clks)... not logging this one..."
			do_add = False
			break
	if do_add:
		if mhz in state.mhz_to_latency:
			if state.total_latency < state.mhz_to_latency[mhz]:
				state.mhz_to_latency[mhz] = state.total_latency		
				state.mhz_to_slices[mhz] = state.current_slices[:]
		else:
			state.mhz_to_latency[mhz] = state.total_latency
			state.mhz_to_slices[mhz] = state.current_slices[:]		
	# IF GOT BEST RESULT SO FAR
	if mhz >= max(state.mhz_to_slices) and not SEEN_CURRENT_SLICES(state):
		# Reset stages adjusted since want full exploration
		print "BEST SO FAR! :)"
		print "Resetting stages adjusted this latency since want full exploration..."
		for stage in range(0, len(state.current_slices) + 1):
			state.stages_adjusted_this_latency[stage] = 0
		
	# wRITE TO LOG
	text = ""
	text += "MHZ	LATENCY	SLICES\n"
	for mhz_i in sorted(state.mhz_to_latency):
		latency = state.mhz_to_latency[mhz_i]
		slices = state.mhz_to_slices[mhz_i]
		text += str(mhz_i) + "	" + str(latency) + "	" + str(slices) + "\n"
	#print text
	f=open(SYN_OUTPUT_DIRECTORY + "/" + "mhz_to_latency_and_slices.log","w")
	f.write(text)
	f.close()
	
	# Write state
	WRITE_SWEEP_STATE_CACHE_FILE(state, Logic)

def SLICE_DISTANCE_MIN(lls):
	return 1.0 / float(lls)

def SLICE_EPSILON(lls):	
	# Saw 
	# 220.945647371	1	[0.4]
	# 215.28525296 1 [0.4054598179598179]
	#        0.4                0.405
	# -------|------------------| 
	# ______220mhz____|_______215_____ 
	#
	# 5 Mhz diff for 0.005459818
	# Total lls was 55 so *4 epsilon was: 
	# 0.004545455
	
	# Needs to be much lower?
	# Half of gap?
	# 0.005459818 / 2 = 0.002729909
	
	# 0.002729909 = 1.0 / (N*55)
	# N = 6.684491979
	# Round? # Nah? Magical number?
	# Sigh
	#
	
	'''
	# Want 4x logic levels worth of adjustment
	num_div = 4 * lls
	'''
	num_div = 6.684491979 * lls
	
	# Fp compare epsilon is this divider
	EPSILON = 1.0 / float(num_div)
	return EPSILON
	
def SLICE_POS_EQ(a,b,epsilon):
	return abs(a-b) < epsilon

def SLICES_EQ(slices_a, slices_b, epsilon):
	# Eq if all values are eq
	if len(slices_a) != len(slices_b):
		return False
	all_eq = True
	for i in range(0,len(slices_a)):
		a = slices_a[i]
		b = slices_b[i]
		if not SLICE_POS_EQ(a,b,epsilon):
			all_eq = False
			break
	
	return all_eq

def SEEN_SLICES(slices, state):
	return GET_EQ_SEEN_SLICES(slices, state) is not None
	
def GET_EQ_SEEN_SLICES(slices, state):
	# Point of this is to check if we have seen these slices
	# But exact floating point equal is dumb
	# Min floaitng point that matters is 1 bit of adjsutment
	# Since multiple bits can fit in logic level
	# Ex. carry4
	
	
	# Check for matching slices in seen slices:
	for slices_i in state.seen_slices:
		if SLICES_EQ(slices_i, slices, SLICE_EPSILON(state.zero_clk_pipeline_map.max_lls_per_stage[0])):
			return slices_i
			
	return None
	
def ROUND_SLICES_TO_SEEN(slices, state):
	rv = slices[:]
	equal_seen_slices = GET_EQ_SEEN_SLICES(slices, state)
	if equal_seen_slices is not None:
		rv =  equal_seen_slices
	return rv
	

def SEEN_CURRENT_SLICES(state):
	return SEEN_SLICES(state.current_slices, state)
	
def FILTER_OUT_SEEN_ADJUSTMENTS(possible_adjusted_slices, state):
	unseen_possible_adjusted_slices = []
	for possible_slices in possible_adjusted_slices:
		if not SEEN_SLICES(possible_slices,state):
			unseen_possible_adjusted_slices.append(possible_slices)
		else:
			print "	Saw,",possible_slices,"already"
	return unseen_possible_adjusted_slices

def DO_THROUGHPUT_SWEEP(Logic, parser_state, target_mhz):
	LogicInstLookupTable = parser_state.LogicInstLookupTable
	FuncLogicLookupTable = parser_state.FuncName2Logic
	print "Instance:",Logic.inst_name
	
	zero_clk_pipeline_map = GET_ZERO_CLK_PIPELINE_MAP(Logic, parser_state)
	print "Pipeline Map:"
	for stage in zero_clk_pipeline_map.stage_per_ll_submodules_map:
		for ll in zero_clk_pipeline_map.stage_per_ll_submodules_map[stage]:
			print "LL:",ll,"	",zero_clk_pipeline_map.stage_per_ll_submodules_map[stage][ll]
	print ""
	orig_total_lls = zero_clk_pipeline_map.max_lls_per_stage[0]
	print "Total LLs In Pipeline Map:",orig_total_lls
	slice_ep = SLICE_EPSILON(zero_clk_pipeline_map.max_lls_per_stage[0])
	print "SLICE EPSILON:", slice_ep
	# START WITH NO SLICES
	
	# Default sweep state is zero clocks ... this is cleaner
	state = GET_MOST_RECENT_OR_DEFAULT_SWEEP_STATE(Logic, zero_clk_pipeline_map)
	
	# Make adjustments via working stage range
	state.working_stage_range = state.stage_range[:]
	
	# Begin the loop of synthesizing adjustments to pick best one
	while True:
		print "|||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||"
		print "|||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||"	
				
		# We have a stage range to work with
		print "Geting default adjustments for these slices:"
		print "Current slices:", state.current_slices
		print "Working stage range:", state.working_stage_range
		print "Slice Step:", state.slice_step
		# If we just got the best result (not seen yet) then try all possible changes by overriding stage range
		if not SEEN_CURRENT_SLICES(state):
			# Sanity check that never missing a peak we climb
			if state.total_latency in state.latency_2_best_slices:
				if SLICES_EQ(state.current_slices , state.latency_2_best_slices[state.total_latency], slice_ep):
					print "Got best result so far, dumb checking all possible adjustments from here."
					state.working_stage_range = range(0,state.total_latency+1)
					print "New working stage range:", state.working_stage_range
			
		print "Adjusting based on working stage range:", state.working_stage_range			
		# Record that we are adjusting stages in the stage range
		#for stage in state.working_stage_range:
		for stage in state.stage_range: # Not working range since want to only count adjustments based on syn results
			state.stages_adjusted_this_latency[stage] += 1

		# Sometimes all possible changes are the same result and best slices will be none
		# Get default set of possible adjustments
		possible_adjusted_slices = GET_DEFAULT_SLICE_ADJUSTMENTS(Logic, state.current_slices,state.working_stage_range, parser_state, state.zero_clk_pipeline_map, state.slice_step, slice_ep, SLICE_DISTANCE_MIN(state.zero_clk_pipeline_map.max_lls_per_stage[0]))
		print "Possible adjustments:"
		for possible_slices in possible_adjusted_slices:
			print "	", possible_slices
		
		# Round slices to nearest if seen before
		new_possible_adjusted_slices = []
		for possible_slices in possible_adjusted_slices:
			new_possible_slices = ROUND_SLICES_TO_SEEN(possible_slices, state)
			new_possible_adjusted_slices.append(new_possible_slices)
		possible_adjusted_slices = REMOVE_DUP_SLICES(new_possible_adjusted_slices, slice_ep)
		print "Possible adjustments after rounding to seen slices:"
		for possible_slices in possible_adjusted_slices:
			print "	", possible_slices
		
		# Add the current slices result to list
		if not SEEN_CURRENT_SLICES(state):
			# Only if not seen yet
			state.seen_slices.append(state.current_slices[:])
		
		print "Running syn for all possible adjustments..."
		state = PARALLEL_SYN_WITH_SLICES_PICK_BEST(Logic, state, parser_state, possible_adjusted_slices)	

		
		
		#__/\\\___________________/\\\\\__________/\\\\\\\\\\\\_        
		# _\/\\\_________________/\\\///\\\______/\\\//////////__       
		#  _\/\\\_______________/\\\/__\///\\\___/\\\_____________      
		#   _\/\\\______________/\\\______\//\\\_\/\\\____/\\\\\\\_     
		#    _\/\\\_____________\/\\\_______\/\\\_\/\\\___\/////\\\_    
		#     _\/\\\_____________\//\\\______/\\\__\/\\\_______\/\\\_   
		#      _\/\\\______________\///\\\__/\\\____\/\\\_______\/\\\_  
		#       _\/\\\\\\\\\\\\\\\____\///\\\\\/_____\//\\\\\\\\\\\\/__ 
		#        _\///////////////_______\/////________\////////////____
		print "Best result out of possible adjustments:"
		LOG_SWEEP_STATE(state, Logic)
		
		# Are we done now?
		curr_mhz = 1000.0 / state.latency_2_best_delay[state.total_latency]
		if curr_mhz >= target_mhz:
			print "Reached target operating frequency of",target_mhz,"MHz"
			return state.TimingParamsLookupTable
		
		
				
		#__/\\\\\\\\\\\__/\\\\____________/\\\\__/\\\\\\\\\\\\\______/\\\\\\\\\___________/\\\\\_______/\\\________/\\\__/\\\\\\\\\\\\\\\_        
		# _\/////\\\///__\/\\\\\\________/\\\\\\_\/\\\/////////\\\__/\\\///////\\\_______/\\\///\\\____\/\\\_______\/\\\_\/\\\///////////__       
		#  _____\/\\\_____\/\\\//\\\____/\\\//\\\_\/\\\_______\/\\\_\/\\\_____\/\\\_____/\\\/__\///\\\__\//\\\______/\\\__\/\\\_____________      
		#   _____\/\\\_____\/\\\\///\\\/\\\/_\/\\\_\/\\\\\\\\\\\\\/__\/\\\\\\\\\\\/_____/\\\______\//\\\__\//\\\____/\\\___\/\\\\\\\\\\\_____     
		#    _____\/\\\_____\/\\\__\///\\\/___\/\\\_\/\\\/////////____\/\\\//////\\\____\/\\\_______\/\\\___\//\\\__/\\\____\/\\\///////______    
		#     _____\/\\\_____\/\\\____\///_____\/\\\_\/\\\_____________\/\\\____\//\\\___\//\\\______/\\\_____\//\\\/\\\_____\/\\\_____________   
		#      _____\/\\\_____\/\\\_____________\/\\\_\/\\\_____________\/\\\_____\//\\\___\///\\\__/\\\________\//\\\\\______\/\\\_____________  
		#       __/\\\\\\\\\\\_\/\\\_____________\/\\\_\/\\\_____________\/\\\______\//\\\____\///\\\\\/__________\//\\\_______\/\\\\\\\\\\\\\\\_ 
		#        _\///////////__\///______________\///__\///______________\///________\///_______\/////_____________\///________\///////////////__
		# Only need to do additional improvement if best of possible adjustments has been seen already
		if not SEEN_CURRENT_SLICES(state):
			# Not seen before, OK to skip additional adjustment
			continue
			
			
		# SEEN THIS BEST state.current_slices BEFORE
		# DO adjustment to get out of loop
		# HOW DO WE ADJUST SLICES??????	
		print "Saw best result of possible adjustments... getting different stage range / slice step"
			
		# HACKY temp thing
		best_ever_latency_to_mhz = dict()
		'''
		best_ever_latency_to_mhz[0] = 120 
		best_ever_latency_to_mhz[1] = 222 
		best_ever_latency_to_mhz[2] = 344
		best_ever_latency_to_mhz[3] = 395
		best_ever_latency_to_mhz[4] = 470
		best_ever_latency_to_mhz[5] = 564
		best_ever_latency_to_mhz[6] = 592
		best_ever_latency_to_mhz[7] = 612 # Took forever and didnt reach 614 again so 612?
		best_ever_latency_to_mhz[8] = 674 #649 # Guess beat Xil
		#best_ever_latency_to_mhz[9] = 650 # Buess beat 8clk?
		#best_ever_latency_to_mhz[10] = 651 # Buess beat 9clk?
		#best_ever_latency_to_mhz[11] = 746 # Guess beat xil
		'''
		
		best_ever_adj = None
		if state.total_latency in best_ever_latency_to_mhz:
			best_mhz = best_ever_latency_to_mhz[state.total_latency]
			best_seen_mhz = 1000.0 /state.latency_2_best_delay[state.total_latency] 
			# If currently at or better then ok to stop
			if best_seen_mhz >= best_mhz: 
				best_ever_adj = state.latency_2_best_slices[state.total_latency]
				
				
		print "TODO: Syn each pipeline stage (from top level main, so easy) individually and use delays?"
				

		if (not best_ever_adj is None):
			print "HACKY ALREADY KNOW BEST SO FAR STOPPING"
			state.current_slices = best_ever_adj[:]
		
		# IF LOOPED BACK TO BEST THEN # Make slice step large to get away from looping back //////CONTINUE REPEATING ADJUSTMENT UNTIL ONE IS NOT SEEN YET
		elif SLICES_EQ(state.current_slices , state.latency_2_best_slices[state.total_latency], slice_ep) and state.total_latency > 1: # 0 or 1 slice will always loop back to best
			print "Looped back to best result, decreasing LLs step to narrow in on best..."
			print "Reducing slice_step..."
			old_slice_step = state.slice_step
			state.slice_step = REDUCE_SLICE_STEP(state.slice_step, state.total_latency, slice_ep)
			print "New slice_step:",state.slice_step
			if old_slice_step == state.slice_step:
				print "Can't reduce slice step any further..."
				# Do nothing and end up adding pipeline stage
				pass
			else:
				# OK to try and get another set of slices
				continue
			
		# BACKUP PLAN TO AVOID LOOPS
		elif state.total_latency > 0:
			print "Reducing slice_step..."
			state.slice_step = REDUCE_SLICE_STEP(state.slice_step, state.total_latency, slice_ep)
			print "New slice_step:",state.slice_step
			# Check for unadjsuted stages
			# Need difference or just guessing which can go forever
			missing_stages = []
			if min(state.stages_adjusted_this_latency.values()) != max(state.stages_adjusted_this_latency.values()):
				print "Checking if all stages have been adjusted..."
				# Find min adjusted count 
				min_adj = 99999999
				for i in range(0, state.total_latency+1):
						adj = state.stages_adjusted_this_latency[i]
						print "Stage",i,"adjusted:",adj
						if adj < min_adj:
							min_adj = adj
				
				# Get stages matching same minimum 
				for i in range(0, state.total_latency+1):
						adj = state.stages_adjusted_this_latency[i]
						if adj == min_adj:
							missing_stages.append(i)
			
			# Only do multiplied adjustments if adjusted all stages at least once
			# Otherwise get caught making stupid adjustments right after init
			actual_min_adj = min(state.stages_adjusted_this_latency.values())
			if (actual_min_adj != 0):
				MAGICAL_LIMIT=3
				print "Multiplying suggested change with limit:",MAGICAL_LIMIT
				orig_slice_step = state.slice_step
				n = 1
				# Dont want to increase slice offset and miss fine grain adjustments
				working_slice_step = state.slice_step 
				# This change needs to produce different slices or we will end up in loop
				orig_slices = state.current_slices[:]
				# Want to increase slice step if all adjustments yield same result
				# That should be the case when we iterate this while loop after running syn
				num_par_syns = 0
				while SLICES_EQ(orig_slices, state.current_slices, slice_ep) and (n <= MAGICAL_LIMIT):
					# Start with no possible adjustments
					print "Working slice step:",working_slice_step
					print "Multiplier:", n
					# Keep increasing slice step until get non zero length of possible adjustments
					possible_adjusted_slices = GET_DEFAULT_SLICE_ADJUSTMENTS(Logic, state.current_slices,state.stage_range, parser_state, state.zero_clk_pipeline_map, working_slice_step, slice_ep, SLICE_DISTANCE_MIN(state.zero_clk_pipeline_map.max_lls_per_stage[0]), multiplier=1, include_bad_changes=False)
					# Filter out ones seen already
					possible_adjusted_slices = FILTER_OUT_SEEN_ADJUSTMENTS(possible_adjusted_slices, state)			
							
					if len(possible_adjusted_slices) > 0:
						print "Got some unseen adjusted slices (with working slice step = ", working_slice_step, ")"
						print "Possible adjustments:"
						for possible_slices in possible_adjusted_slices:
							print "	", possible_slices
						print "Picking best out of possible adjusments..."
						state = PARALLEL_SYN_WITH_SLICES_PICK_BEST(Logic, state, parser_state, possible_adjusted_slices)
						num_par_syns += 1
						

					# Increase WORKING slice step
					n = n + 1
					print "Increasing working slice step, multiplier:", n
					working_slice_step = orig_slice_step * n
					print "New working slice_step:",working_slice_step
					
				# If actually got new slices then continue to next run
				if not SLICES_EQ(orig_slices, state.current_slices, slice_ep):
					print "Got new slices, running with those:", state.current_slices
					print "Increasing slice step based on number of syn attempts:", num_par_syns
					state.slice_step = num_par_syns * state.slice_step
					print "New slice_step:",state.slice_step
					LOG_SWEEP_STATE(state, Logic)
					continue
				else:
					print "Did not get any unseen slices to try?"
					print "Now trying backup check of all stages adjusted equally..."
			
			##########################################################
			##########################################################
			
			
			# Allow cutoff if sad
			AM_SAD = (int(open("i_am_sad.cfg",'r').readline()) > 0) and (actual_min_adj > 1)
			if AM_SAD:
				print '''                               
 .-.                           
( __)                          
(''")                          
 | |                           
 | |                           
 | |                           
 | |                           
 | |                           
 | |                           
(___)                          
                               
                               
                               
                               
  .---.   ___ .-. .-.          
 / .-, \ (   )   '   \         
(__) ; |  |  .-.  .-. ;        
  .'`  |  | |  | |  | |        
 / .'| |  | |  | |  | |        
| /  | |  | |  | |  | |        
; |  ; |  | |  | |  | |        
' `-'  |  | |  | |  | |        
`.__.'_. (___)(___)(___)       
                               
                               
                          ___  
                         (   ) 
    .--.      .---.    .-.| |  
  /  _  \    / .-, \  /   \ |  
 . .' `. ;  (__) ; | |  .-. |  
 | '   | |    .'`  | | |  | |  
 _\_`.(___)  / .'| | | |  | |  
(   ). '.   | /  | | | |  | |  
 | |  `\ |  ; |  ; | | '  | |  
 ; '._,' '  ' `-'  | ' `-'  /  
  '.___.'   `.__.'_.  `.__,'   
                                                           
'''
				# Wait until file reads 0 again
				while len(open("i_am_sad.cfg",'r').readline())==1 and (int(open("i_am_sad.cfg",'r').readline()) > 0):
					pass
			
				
			# Do something with missing stage or backup		
			stages_off_balance = ((max(state.stages_adjusted_this_latency.values()) - min(state.stages_adjusted_this_latency.values())) >= len(state.current_slices)) or (actual_min_adj == 0)
			
			if (len(missing_stages) > 0) and stages_off_balance and not AM_SAD:
				print "These stages have not been adjusted much: <<<<<<<<<<< ", missing_stages
				state.working_stage_range = missing_stages
				print "New working stage range:",state.working_stage_range
				EXPAND_UNTIL_SEEN_IN_SYN = True
				if not EXPAND_UNTIL_SEEN_IN_SYN:
					print "So running with working stage range..."
					continue
				else:
					print "<<<<<<<<<<< Expanding missing stages until seen in syn results..."
					local_seen_slices = []
					# Keep expanding those stages until one of them is seen in timing report stage range
					new_stage_range = []
					new_slices = state.current_slices[:]
					found_missing_stages = []
					try_num = 0
					
					while len(found_missing_stages)==0:
						print "<<<<<<<<<<< TRY",try_num
						try_num += 1
						# Do adjustment
						pre_expand_slices = new_slices[:]
						new_slices,new_stages_adjusted_this_latency = EXPAND_STAGES_VIA_ADJ_COUNT(missing_stages, new_slices, state.slice_step, state, SLICE_DISTANCE_MIN(state.zero_clk_pipeline_map.max_lls_per_stage[0]))
						state.stages_adjusted_this_latency = new_stages_adjusted_this_latency
						
						# If expansion is same as current slices then stop
						if SLICES_EQ(new_slices, pre_expand_slices, slice_ep):
							print "Expansion was same as current? Give up..."
							new_slices = None
							break
							
						seen_new_slices_locally = False
						# Check for matching slices in local seen slices:
						for slices_i in local_seen_slices:
							if SLICES_EQ(slices_i, new_slices, slice_ep):
								seen_new_slices_locally = True
								break
												
						if SEEN_SLICES(new_slices, state) or seen_new_slices_locally:
							print "Expanding further, saw these slices already:", new_slices
							continue
							
						print "<<<<<<<<<<< Rounding", new_slices, "away flom globals..."
						pre_round = new_slices[:]
						new_slices = ROUND_SLICES_AWAY_FROM_GLOBAL_LOGIC(Logic, new_slices, state.slice_step, parser_state, state.zero_clk_pipeline_map)
						
						seen_new_slices_locally = False
						# Check for matching slices in local seen slices:
						for slices_i in local_seen_slices:
							if SLICES_EQ(slices_i, new_slices, slice_ep):
								seen_new_slices_locally = True
								break
						
						if SEEN_SLICES(new_slices, state) or seen_new_slices_locally:
							print "Expanding further, saw rounded slices:",new_slices
							new_slices = pre_round
							continue				
						
						print "<<<<<<<<<<< Running syn with expanded slices:", new_slices
						new_stage_range, new_timing_report, new_total_latency, new_TimingParamsLookupTable = DO_SYN_WITH_SLICES(new_slices, Logic, zero_clk_pipeline_map, parser_state)
						print "<<<<<<<<<<< Got stage range:",new_stage_range
						for new_stage in new_stage_range:
							if new_stage in missing_stages:
								found_missing_stages.append(new_stage)
								print "<<<<<<<<<<< Has missing stage", new_stage
						# Record having seen these slices but keep it local yo
						local_seen_slices.append(new_slices)
						
								
					
					if new_slices is not None:
						print "<<<<<<<<<<< Using adjustment", new_slices, "that resulted in missing stage", found_missing_stages
						state.current_slices= new_slices
						state.stage_range = new_stage_range
						state.timing_report = new_timing_report
						state.total_latency = new_total_latency
						state.TimingParamsLookupTable = new_TimingParamsLookupTable
						
						# Slice step should match the total adjustment that was needed?
						print "<<<<<<<<<<< Slice step should match the total adjustment that was needed (based on num tries)?"
						state.slice_step = try_num * state.slice_step
						print "<<<<<<<<<<< Increasing slice step to:", state.slice_step
						LOG_SWEEP_STATE(state, Logic)
						continue
					else:
						print "<<<<<<<<<<< Couldnt shrink other stages anymore? No adjustment made..."
					
			else:
				print "Cannot find missing stage adjustment?"
				
				
					
		
		# Result of either unclear or clear state.stage 
		print "Didn't make any changes?",state.current_slices

		# If seen this config (made no adjsutment) add another pipeline slice
		if SEEN_CURRENT_SLICES(state):
			print "Saw this slice configuration already. We gave up."
			print "Adding another pipeline stage..."
			state = ADD_ANOTHER_PIPELINE_STAGE(Logic, state, parser_state)
			# Run syn to get started with this new set of slices
			# Run syn with these slices
			print "Running syn after adding pipeline stage..."
			#print "Current slices:", state.current_slices
			#print "Slice Step:",state.slice_step
			state.stage_range, state.timing_report, state.total_latency, state.TimingParamsLookupTable = DO_SYN_WITH_SLICES(state.current_slices, Logic, zero_clk_pipeline_map, parser_state)
			mhz = (1.0 / (state.timing_report.data_path_delay / 1000.0)) 
			LOG_SWEEP_STATE(state, Logic)
		else:
			print "What didnt make change and got new slices?", state.current_slices
			sys.exit(0)
			
		
		
	
	sys.exit(0)
	
	

	
def GET_SLICE_PER_STAGE(current_slices):
	# Get list of how many lls per stage
	slice_per_stage = []
	slice_total = 0.0
	for slice_pos in current_slices:
		slice_size = slice_pos - slice_total
		slice_per_stage.append(slice_size)
		slice_total = slice_total + slice_size
	# Add end stage
	slice_size = 1.0 - current_slices[len(current_slices)-1]
	slice_per_stage.append(slice_size)
	
	return slice_per_stage
	
def BUILD_SLICES(slice_per_stage):
	rv = []
	slice_total = 0.0
	for slice_size in slice_per_stage:
		new_slice = slice_size + slice_total
		rv.append(new_slice)
		slice_total = slice_total + slice_size	
	# Remove end slice?
	rv = rv[0:len(rv)-1]
	return rv
	
# Return slices and new copy of adjsuted stages dict
def EXPAND_STAGES_VIA_ADJ_COUNT(missing_stages, current_slices, slice_step, state, min_dist):	
	print "<<<<<<<<<<< EXPANDING", missing_stages, "VIA_ADJ_COUNT:",current_slices
	slice_per_stage = GET_SLICE_PER_STAGE(current_slices)
	# Each missing stage is expanded by slice_step/num missing stages
	# Div by num missing stages since might not be able to shrink enough slices 
	# to account for every missing stage getting slice step removed
	expansion = slice_step / len(missing_stages)
	total_expansion = 0.0
	stages_adjusted_this_latency = copy.deepcopy(state.stages_adjusted_this_latency)
	for missing_stage in missing_stages:
		slice_per_stage[missing_stage] = slice_per_stage[missing_stage] + expansion
		total_expansion = total_expansion + expansion
		# Record this expansion as adjustment
		stages_adjusted_this_latency[missing_stage] += 1
	
	remaining_expansion = total_expansion
	# Split remaining expansion among other stages
	# First check how many stages will be shrank
	stages_to_shrink = []
	for stage in range(0, len(current_slices)+1):
		if not(stage in missing_stages) and (slice_per_stage[stage] - expansion > min_dist):
			stages_to_shrink.append(stage)
	if len(stages_to_shrink) > 0:
		shrink_per_stage = total_expansion / float(len(stages_to_shrink))
		for stage in stages_to_shrink:
			print "Shrinking stage",stage, "curr size:", slice_per_stage[stage]
			slice_per_stage[stage] -= shrink_per_stage
			# Record adjustment
			stages_adjusted_this_latency[stage] += 1		
		
		# reconstruct new slcies
		rv = BUILD_SLICES(slice_per_stage)
		
		#print "RV",rv
		#sys.exit(0)
		
		return rv, stages_adjusted_this_latency
	else:
		# Can't shrink anything more
		return current_slices, state.stages_adjusted_this_latency

	


def ESTIMATE_MAX_THROUGHPUT(mhz_range, mhz_to_latency):
	'''
	High Clock	High Latency	High Period	Low Clock	Low Latency	Low Period	"Required parallel
	Low clock"	"Serialization
	Latency (1 or each clock?) (ns)"
	160	5	6.25	100		10	1.6	16.25
	240	10						
	100	2						
	260	10						
	40	0						
	220	10						
	140	5						
	80	1						
	200	10						
	20	0						
	300	43						
	120	5						
	180	8						
	60	1						
	280	32						
	'''
	min_mhz = min(mhz_range)
	max_mhz = max(mhz_range)
	max_div = int(math.ceil(max_mhz/min_mhz))
	
	text = ""
	text += "clk_mhz" + "	" +"high_clk_latency_ns" + "	" + "div_clk_mhz" + "	" + "div_clock_latency_ns" + "	" + "n_parallel" + "	" + "deser_delay" + "	" + "ser_delay" + "	" + "total_latency" + "\n"
	
	# For each clock we have calculate the integer divided clocks
	for clk_mhz in mhz_to_latency:
		# Orig latency
		clk_period_ns = (1.0/clk_mhz)*1000
		high_clk_latency_ns = mhz_to_latency[clk_mhz] * clk_period_ns
		# Find interger divided clocks we have entries for
		for div_i in range(2,max_div+1):
			div_clk_mhz =  clk_mhz / div_i
			if div_clk_mhz in mhz_to_latency:
				# Have div clock entry
				n_parallel = div_i
				div_clk_period_ns = (1.0/div_clk_mhz)*1000
				# Deserialize
				deser_delay = clk_period_ns + div_clk_period_ns # one of each clock?
				# Latency is one of the low clock
				div_clock_latency_clks = mhz_to_latency[div_clk_mhz]
				div_clock_latency_ns = div_clock_latency_clks * div_clk_period_ns
				# Serialize
				ser_delay = clk_period_ns + div_clk_period_ns # one of each clock?
				# total latency
				total_latency  = deser_delay + div_clock_latency_ns + ser_delay
				
				text += str(clk_mhz) + "	" + str(high_clk_latency_ns) + "	" + str(div_clk_mhz) + "	" + str(div_clock_latency_ns) + "	" + str(n_parallel) + "	" + str(deser_delay) + "	" + str(ser_delay) + "	" + str(total_latency) + "\n"
		
	
	f = open(SYN_OUTPUT_DIRECTORY + "/" + "estimated_max_throughput.log","w")
	f.write(text)
	f.close()


def GET_OUTPUT_DIRECTORY(Logic, implement):
	if implement:
		print "No implement foo"
		sys.exit(0)
		#output_directory = IMP_OUTPUT_DIRECTORY + "/" + Logic.inst_name.replace(C_TO_LOGIC.SUBMODULE_MARKER, "/")
	else:
		# Syn
		 output_directory = SYN_OUTPUT_DIRECTORY + "/" + Logic.inst_name.replace(C_TO_LOGIC.SUBMODULE_MARKER, "/").replace(C_TO_LOGIC.REF_TOK_DELIM,"_REF_")
		#output_directory = SYN_OUTPUT_DIRECTORY + "/" + VHDL.GET_INST_NAME(Logic, use_leaf_name=True)
	return output_directory





	


def IS_BITWISE_OP(logic):
	if logic.func_name=="main" and logic.inst_name=="main":
		return False
	elif logic.is_c_built_in==False:
		# Not built in C is assumed not bit manip
		return False
	elif logic.func_name.startswith(C_TO_LOGIC.UNARY_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.UNARY_OP_NOT_NAME):
		return True
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_AND_NAME):
		return True
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_OR_NAME):
		return True
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_XOR_NAME):
		return True
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_PLUS_NAME):
		return False
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_MINUS_NAME):
		return False
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_MULT_NAME):
		return False
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_DIV_NAME):
		return False
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_GT_NAME):
		return False
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_EQ_NAME):
		return False
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_SL_NAME):
		return False
	elif logic.func_name.startswith(C_TO_LOGIC.BIN_OP_LOGIC_NAME_PREFIX + "_" + C_TO_LOGIC.BIN_OP_SR_NAME):
		return False
	elif logic.func_name == C_TO_LOGIC.MUX_LOGIC_NAME:
		return False
	elif (logic.func_name.startswith(VHDL_INSERT.HDL_INSERT + "_" + VHDL_INSERT.STRUCTREF_RD) or
		  logic.func_name.startswith(VHDL_INSERT.HDL_INSERT + "_" + VHDL_INSERT.STRUCTREF_WR) ):	
		return False
	#elif logic.func_name.startswith(C_TO_LOGIC.STRUCT_RD_FUNC_NAME_PREFIX):
	#	return False
	#elif logic.func_name.startswith(C_TO_LOGIC.ARRAY_REF_CONST_FUNC_NAME_PREFIX):
	#	return False
	elif logic.func_name.startswith(C_TO_LOGIC.CONST_REF_RD_FUNC_NAME_PREFIX):
		return False
	else:
		print "Is logic.func_name", logic.func_name, "a bitwise operator?"
		sys.exit(0)	
	
def IS_USER_CODE(logic):
	# Check logic.is_built_in
	# or autogenerated code	
	
	# Also check types of inputs are ints
	input_types = []
	for input_wire in logic.inputs:
		input_types.append(logic.wire_to_c_type[input_wire])
	
	# Submodules whose inputs are not integers must be user code since only C types supported natively are ints?
	### GAH float is built in too?
	#TODO
	#not_user_code = logic.is_c_built_in or SW_LIB.IS_AUTO_GENERATED(logic)
	#return not not_user_code

	
	user_code = (not logic.is_c_built_in and not SW_LIB.IS_AUTO_GENERATED(logic)) or (not VHDL.C_TYPES_ARE_INTEGERS(input_types))
	return user_code
		
		
def GET_CACHED_TOTAL_LOGIC_LEVELS_FILE_PATH(logic):
	key = logic.func_name
	
	# hack ycheck for mux since # TODO: do for all bit math?
	toks = logic.func_name.split("_")
	is_nmux = False
	
	# TODO: update this for nsum
	if len(toks) == 2:
		is_nmux = (toks[0].startswith("uint") or toks[0].startswith("int")) and toks[1].startswith("mux")
		# Wow so hacky
	
	if not is_nmux:
		for input_wire in logic.inputs:
			c_type = logic.wire_to_c_type[input_wire]
			key += "_" + c_type
		
	file_path = LLS_CACHE_DIR + "/" + key + ".lls"
	
	return file_path
	
def GET_CACHED_TOTAL_LOGIC_LEVELS(logic):	
	# Look in cache dir
	file_path = GET_CACHED_TOTAL_LOGIC_LEVELS_FILE_PATH(logic)
	if os.path.exists(file_path):
		print "Reading Cached LLs File:", file_path
		return int(open(file_path,"r").readlines()[0])
		
	return None	
	
	
def ADD_TOTAL_LOGIC_LEVELS_TO_LOOKUP(main_logic, parser_state):
	# initial params are 0 clk latency for all submodules
	TimingParamsLookupTable = dict()
	for logic_inst_name in parser_state.LogicInstLookupTable: 
		logic = parser_state.LogicInstLookupTable[logic_inst_name]
		timing_params = TimingParams(logic)
		TimingParamsLookupTable[logic_inst_name] = timing_params	
		
	print "Recursively Writing VHDL files for all instances starting at main..."
	RECURSIVE_WRITE_ALL_VHDL_PROCEDURE_PACKAGES(main_logic, parser_state, TimingParamsLookupTable)
	
	print "Writing the constant struct+enum definitions as defined from C code..."
	VHDL.WRITE_C_DEFINED_VHDL_STRUCTS_PACKAGE(parser_state)
	
	
	
	print "Synthesizing at 0 latency to get total logic levels..."
	print ""
	
	# Record stats on functions with globals
	min_mhz = 999999999
	min_mhz_func_name = None	
	
	#bAAAAAAAAAAAhhhhhhh need to recursively do this 
	# Do depth first 
	# haha no do it the dumb easy way
	logic_inst_done_so_far = []
	all_logic_inst_done = False
	while not(all_logic_inst_done):
		all_logic_inst_done = True
		#print "parser_state.LogicInstLookupTable",parser_state.LogicInstLookupTable
		
		#print "Synthesizing logic..."
		#raw_input("[ENTER]")
		for logic_inst_name in parser_state.LogicInstLookupTable:
			# If already done then skip
			if logic_inst_name in logic_inst_done_so_far:
				continue

			# Get logic
			logic = parser_state.LogicInstLookupTable[logic_inst_name]
			
			# All dependencies met?
			all_dep_met = True
			for submodule_inst in logic.submodule_instances:
				if not(submodule_inst in logic_inst_done_so_far):
					#print "submodule_inst not in logic_inst_done_so_far", submodule_inst
					#print "submodule_inst in parser_state.LogicInstLookupTable",submodule_inst in parser_state.LogicInstLookupTable
					all_dep_met = False
					
			
			# If not all dependencies met then dont do syn yet
			if not(all_dep_met):
				#print "not(all_dep_met) logic.inst_name", logic.inst_name
				all_logic_inst_done = False
				continue
			
			# Do syn for logic we can't already guess/is know
			# Bit manip raw hdl
			print "Instance:", GET_OUTPUT_DIRECTORY(logic,False).replace(SYN_OUTPUT_DIRECTORY,"")
			
			# Try to get cached total logic levels
			cached_total_logic_levels = GET_CACHED_TOTAL_LOGIC_LEVELS(logic)

			if str(logic.c_ast_node.coord).split(":")[0].endswith(SW_LIB.BIT_MANIP_HEADER_FILE):
				logic.total_logic_levels = 0
				print "BIT MANIP:", C_TO_LOGIC.LEAF_NAME(logic.inst_name)," TOTAL LOGIC LEVELS:", logic.total_logic_levels
			# Bitwise binary ops
			elif IS_BITWISE_OP(logic):
				logic.total_logic_levels = 1
				print "BITWISE OP:", C_TO_LOGIC.LEAF_NAME(logic.inst_name)," TOTAL LOGIC LEVELS:", logic.total_logic_levels
			elif logic.func_name.startswith(C_TO_LOGIC.MUX_LOGIC_NAME) and len(logic.inputs) == 3: # Cond input too
				logic.total_logic_levels = 1
				print "2 Input MUX:", C_TO_LOGIC.LEAF_NAME(logic.inst_name)," TOTAL LOGIC LEVELS:", logic.total_logic_levels
			## STRUCT READ
			#elif logic.func_name.startswith(C_TO_LOGIC.STRUCT_RD_FUNC_NAME_PREFIX):
			#	logic.total_logic_levels = 0
			#	print "STRUCT READ:", C_TO_LOGIC.LEAF_NAME(logic.inst_name)," TOTAL LOGIC LEVELS:", logic.total_logic_levels
			## CONST ARRAY REF
			#elif logic.func_name.startswith(C_TO_LOGIC.ARRAY_REF_CONST_FUNC_NAME_PREFIX):
			#	logic.total_logic_levels = 0
			#	print "CONST ARRAY REF:", C_TO_LOGIC.LEAF_NAME(logic.inst_name)," TOTAL LOGIC LEVELS:", logic.total_logic_levels
				
			elif logic.func_name.startswith(C_TO_LOGIC.CONST_REF_RD_FUNC_NAME_PREFIX):
				logic.total_logic_levels = 0
				print "CONST REF:", C_TO_LOGIC.LEAF_NAME(logic.inst_name)," TOTAL LOGIC LEVELS:", logic.total_logic_levels
				
			# 0 LLs HDL insert lookup table
			elif (logic.func_name.startswith(VHDL_INSERT.HDL_INSERT + "_" + VHDL_INSERT.STRUCTREF_RD) or
			      logic.func_name.startswith(VHDL_INSERT.HDL_INSERT + "_" + VHDL_INSERT.STRUCTREF_WR) ):
				logic.total_logic_levels = 0
				print "0LL HDL Insert:", C_TO_LOGIC.LEAF_NAME(logic.inst_name)," TOTAL LOGIC LEVELS:", logic.total_logic_levels			
			elif not(cached_total_logic_levels is None):
				logic.total_logic_levels = cached_total_logic_levels
				print "Cached:", C_TO_LOGIC.LEAF_NAME(logic.inst_name)," TOTAL LOGIC LEVELS:", logic.total_logic_levels
			else:
				clock_mhz = INF_MHZ # Impossible goal for timing since jsut logic levels
				implement = False
				print "SYN (leaf name):", C_TO_LOGIC.LEAF_NAME(logic.inst_name)
				parsed_timing_report = VIVADO.SYN_IMP_AND_REPORT_TIMING(logic, parser_state, TimingParamsLookupTable, implement, clock_mhz, total_latency=0)
				if parsed_timing_report.logic_levels is None:
					print "Cannot synthesize for total logic levels ",logic.inst_name
					print parsed_timing_report.orig_text
					if DO_SYN_FAIL_SIM:
						MODELSIM.DO_OPTIONAL_DEBUG(do_debug=True)
					sys.exit(0)
				mhz = 1000.0 / parsed_timing_report.data_path_delay
				logic.total_logic_levels = parsed_timing_report.logic_levels
				print "SYN:", C_TO_LOGIC.LEAF_NAME(logic.inst_name)," TOTAL LOGIC LEVELS:", logic.total_logic_levels, "MHz:",mhz
				
				# Record worst global
				if len(logic.global_wires) > 0:
					if mhz < min_mhz:
						min_mhz_func_name = logic.func_name
						min_mhz = mhz
				
				
				# Cache total logic levels syn result if not user code
				if not IS_USER_CODE(logic) and not(logic.func_name.startswith(C_TO_LOGIC.CONST_REF_RD_FUNC_NAME_PREFIX)):
					filepath = GET_CACHED_TOTAL_LOGIC_LEVELS_FILE_PATH(logic)
					if not os.path.exists(LLS_CACHE_DIR):
						os.makedirs(LLS_CACHE_DIR)					
					f=open(filepath,"w")
					f.write(str(logic.total_logic_levels))
					f.close()
				
			
			# Save logic with total_logic_levels into lookup
			parser_state.LogicInstLookupTable[logic_inst_name] = logic
			
			# Done
			logic_inst_done_so_far.append(logic_inst_name)
			
			print ""
			

	# Record worst global
	if min_mhz_func_name is not None:
		print "Design limited to ~", min_mhz, "MHz due to function:", min_mhz_func_name
		parser_state.global_mhz_limit = min_mhz
		
	return parser_state.LogicInstLookupTable

	
	
# Generalizing is a bad thing to do
# Abstracting is something more


# Returns updated timing params dict
def REBUILD_TIMING_PARAMS_AND_WRITE_VHDL_PACKAGES_BOTTOM_UP(submodule_logic_inst_name, parser_state, TimingParamsLookupTable, seen_submodule_logic_inst_names = [], write_files=True):	
	# Get logic for submodule
	submodule_logic = parser_state.LogicInstLookupTable[submodule_logic_inst_name]
	# Get timing params for submodule
	submodule_timing_params = TimingParamsLookupTable[submodule_logic_inst_name]
	
	if write_files:
		# Write VHDL file for submodule
		# Re write submodule package with updated timing params
		syn_out_dir = GET_OUTPUT_DIRECTORY(submodule_logic, implement=False)
		if not os.path.exists(syn_out_dir):
			os.makedirs(syn_out_dir)		
		VHDL.GENERATE_PACKAGE_FILE(submodule_logic, parser_state, TimingParamsLookupTable, submodule_timing_params, syn_out_dir)

	# Get the container for this submodule
	container_logic = C_TO_LOGIC.GET_CONTAINER_LOGIC_FOR_SUBMODULE_INST(submodule_logic_inst_name,parser_state.LogicInstLookupTable)
	if not(container_logic is None):
		
		#### DONT NEED container based propogation any more since get latency does the recursion as necessary?
		'''
		# Has a container
		# Get latency of the submodule
		new_submodule_latency = submodule_timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
		# Set latency of submodule in container logic
		timing_params = TimingParamsLookupTable[container_logic.inst_name]
		# Set latency in containing submodule
		timing_params.SET_SUBMODULE_LATENCY(submodule_logic_inst_name, new_submodule_latency)
		# Final adjustment to lookup table
		TimingParamsLookupTable[container_logic.inst_name]=timing_params
		'''
		
		
		# Move up hierarchy starting at the container
		TimingParamsLookupTable = REBUILD_TIMING_PARAMS_AND_WRITE_VHDL_PACKAGES_BOTTOM_UP(container_logic.inst_name,parser_state, TimingParamsLookupTable, seen_submodule_logic_inst_names)
		
	else:
		# This module has no container, done?
		pass
		
	# Record seeing this submodule
	seen_submodule_logic_inst_names.append(submodule_logic_inst_name)
		
	
	return TimingParamsLookupTable
	


def RECURSIVE_WRITE_ALL_VHDL_PROCEDURE_PACKAGES(main_logic, parser_state, TimingParamsLookupTable):	
	for submodule_inst in main_logic.submodule_instances:
		#print "...",submodule_inst
		submodule_logic = parser_state.LogicInstLookupTable[submodule_inst]
		RECURSIVE_WRITE_ALL_VHDL_PROCEDURE_PACKAGES(submodule_logic, parser_state, TimingParamsLookupTable)	
	
	# Write package if is not HDL INSERT (aka no package file)
	if not main_logic.func_name.startswith(VHDL_INSERT.HDL_INSERT):
		timing_params = TimingParamsLookupTable[main_logic.inst_name]	
		syn_out_dir = GET_OUTPUT_DIRECTORY(main_logic, implement=False)
		if not os.path.exists(syn_out_dir):
			os.makedirs(syn_out_dir)			
		VHDL.GENERATE_PACKAGE_FILE(main_logic, parser_state, TimingParamsLookupTable, timing_params, syn_out_dir)
		
def GET_WORST_PATH_RAW_HDL_SUBMODULE_INST(logic, parsed_timing_report, LogicLookupTable, TimingParamsLookupTable):
	# Get the string names for the start and end regs
	# Return none if start and ends are not clocked elements
	start_reg_name = parsed_timing_report.start_reg_name
	end_reg_name = parsed_timing_report.end_reg_name
	if (start_reg_name is None) or (end_reg_name is None):
		# Means either start or end point isnt clocked
		print "What to do here? if (start_reg_name is None) or (end_reg_name is None):"
		sys.exit(0)
	else:
		# Both are registers
		# Decode to logic hierachy down to parsed C name based on VHDL writing code
		worst_path_raw_hdl_submodule = VIVADO.GET_WORST_PATH_RAW_HDL_SUBMODULE_INST_FROM_SYN_REG_NAMES(logic, start_reg_name, end_reg_name, LogicLookupTable, TimingParamsLookupTable, parsed_timing_report)
		return worst_path_raw_hdl_submodule
	

	

def GET_ABS_SUBMODULE_STAGE_WHEN_USED(submodule_inst_name, logic, parser_state, TimingParamsLookupTable):
	LogicInstLookupTable = parser_state.LogicInstLookupTable
	# Start at contingin logic
	absolute_stage = 0
	curr_submodule_inst = submodule_inst_name

	# Stop once at top level
	# DO
	while curr_submodule_inst != logic.inst_name:
		#print "curr_submodule_inst",curr_submodule_inst
		#print "logic.inst_name",logic.inst_name
		#print "GET_ABS_SUBMODULE_STAGE_WHEN_USED"
		#print "curr_submodule_inst",curr_submodule_inst
		curr_container_logic = C_TO_LOGIC.GET_CONTAINER_LOGIC_FOR_SUBMODULE_INST(curr_submodule_inst, LogicInstLookupTable)
		containing_timing_params = TimingParamsLookupTable[curr_container_logic.inst_name]
		
		local_stage_when_used = GET_LOCAL_SUBMODULE_STAGE_WHEN_USED(curr_submodule_inst, curr_container_logic, parser_state, TimingParamsLookupTable)
		absolute_stage += local_stage_when_used
		
		# Update vars for next iter
		curr_submodule_inst = curr_container_logic.inst_name
		
	return absolute_stage
		

def GET_LOCAL_SUBMODULE_STAGE_WHEN_USED(submodule_inst, container_logic, parser_state, TimingParamsLookupTable):	
	timing_params = TimingParamsLookupTable[container_logic.inst_name]	
	
	pipeline_map = GET_PIPELINE_MAP(container_logic, parser_state, TimingParamsLookupTable)
	per_stage_time_order = pipeline_map.stage_ordered_submodule_list
	#per_stage_time_order = VHDL.GET_PER_STAGE_LOCAL_TIME_ORDER_SUBMODULE_INSTANCE_NAMES(container_logic, parser_state, TimingParamsLookupTable)
	
	# Find earliest occurance of this submodule inst in per stage order
	earliest_stage = TimingParamsLookupTable[container_logic.inst_name].GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
	earliest_match = None
	for stage in per_stage_time_order:
		for c_built_in_inst_name in per_stage_time_order[stage]:
			if submodule_inst in c_built_in_inst_name:
				if stage <= earliest_stage:
					earliest_match = c_built_in_inst_name
					earliest_stage = stage
	
	if earliest_match is None:
		print "What stage when used?", submodule_inst
		print "per_stage_time_order",per_stage_time_order
		sys.exit(0)
		
	return earliest_stage

'''
def GET_LOCAL_SUBMODULE_STAGE_WHEN_COMPLETE(submodule_inst, container_logic, parser_state, TimingParamsLookupTable):
	# Add latency to when sued
	used = GET_LOCAL_SUBMODULE_STAGE_WHEN_USED(submodule_inst, container_logic, parser_state, TimingParamsLookupTable)
	#submodule_logic = LogicInstLookupTable[submodule_inst]
	return used + TimingParamsLookupTable[submodule_inst].GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
'''	

def GET_ABS_SUBMODULE_STAGE_WHEN_COMPLETE(submodule_inst_name, logic, LogicInstLookupTable, TimingParamsLookupTable):
	# Add latency to when sued
	used = GET_ABS_SUBMODULE_STAGE_WHEN_USED(submodule_inst_name, logic, parser_state, TimingParamsLookupTable)
	#submodule_logic = LogicInstLookupTable[submodule_inst]
	return used + TimingParamsLookupTable[submodule_inst_name].GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
	
def GET_SUBMODULE_EST_LLS_PER_STAGE_DICT(TimingParamsLookupTable, LogicInstLookupTableWLogicLevels):
	rv = dict()
	# Loop over all logic and find c built in
	for logic_inst in LogicInstLookupTableWLogicLevels:
		logic = LogicInstLookupTableWLogicLevels[logic_inst]
		timing_params = TimingParamsLookupTable[logic_inst]
		for sub_inst_name in logic.submodule_instances:
			num_stages = timing_params.GET_SUBMODULE_LATENCY(sub_inst_name) + 1
			total_logic_levels = LogicInstLookupTableWLogicLevels[sub_inst_name].total_logic_levels
			slice_per_stage = float(total_logic_levels) / float(num_stages)
			rv[sub_inst_name] = slice_per_stage
	return rv

	
def GET_WIRE_NAME_FROM_WRITE_PIPE_VAR_NAME(wire_write_pipe_var_name, logic):
	# Reg name can be wire name or submodule and port
	#print "GET_WIRE_NAME_FROM_WRITE_PIPE_VAR_NAME", wire_reg_name
	# Check for submodule inst in name
	for submodule_inst_name in logic.submodule_instances:
		if C_TO_LOGIC.LEAF_NAME(submodule_inst_name) in wire_write_pipe_var_name:
			# Port name is without inst name
			port_name = wire_write_pipe_var_name.replace(C_TO_LOGIC.LEAF_NAME(submodule_inst_name)+"_", "")
			wire_name = submodule_inst_name + C_TO_LOGIC.SUBMODULE_MARKER + port_name
			return wire_name
			
	# Not a submodule port wire 
	print "GET_WIRE_NAME_FROM_WRITE_PIPE_VAR_NAME Not a submodule port wire ?"
	print "wire_write_pipe_var_name",wire_write_pipe_var_name
	sys.exit(0)
		
def GET_CACHED_PIPELINE_MAP(Logic, TimingParamsLookupTable, LogicInstLookupTable):
	output_directory = GET_OUTPUT_DIRECTORY(Logic, implement=False)
	timing_params = TimingParamsLookupTable[Logic.inst_name]
	filename = Logic.inst_name.replace("/","_") + "_" + timing_params.GET_HASH_EXT(TimingParamsLookupTable, parser_state) + ".pipe"
	filepath = output_directory + "/" + filename
	if os.path.exists(filepath):
		with open(filepath, 'rb') as input:
			try:
				pipe_line_map = pickle.load(input)
			except:
				print "WTF? cant load pipeline map file",filepath
				print sys.exc_info()[0]                                                                
				#sys.exit(0)
				return None								
			return pipe_line_map
	return None	
	
	
def WRITE_PIPELINE_MAP_CACHE_FILE(Logic, pipeline_map, TimingParamsLookupTable, parser_state):
	output_directory = GET_OUTPUT_DIRECTORY(Logic, implement=False)
	timing_params = TimingParamsLookupTable[Logic.inst_name]
	filename = Logic.inst_name.replace("/","_") + "_" + timing_params.GET_HASH_EXT(TimingParamsLookupTable, parser_state) + ".pipe"
	filepath = output_directory + "/" + filename
	# Write dir first if needed
	if not os.path.exists(output_directory):
		os.makedirs(output_directory)
		
	# Write file
	with open(filepath, 'w') as output:
		pickle.dump(pipeline_map, output, pickle.HIGHEST_PROTOCOL)
	
	
# Identify stage range of raw hdl registers in the pipeline
def GET_START_STAGE_END_STAGE_FROM_REGS(logic, start_reg_name, end_reg_name, LogicLookupTable, TimingParamsLookupTable, parsed_timing_report):
	# Worst path is found by searching a range of pipeline stages for the submodule with the highest LLs per stage
	# Default smallest search range
	timing_params = TimingParamsLookupTable[logic.inst_name]
	start_stage = timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
	end_stage = 0
	
	DO_WHOLE_PIPELINE = True
	if DO_WHOLE_PIPELINE:
		start_stage = 0
		end_stage = timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
		return start_stage, end_stage
	
	if VIVADO.REG_NAME_IS_INPUT_REG(start_reg_name) and VIVADO.REG_NAME_IS_OUTPUT_REG(end_reg_name):
		print "	Comb path to and from register in top.vhd"
		# Search entire space (should be 0 clk anyway)
		start_stage = 0
		end_stage = timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)	
	elif VIVADO.REG_NAME_IS_INPUT_REG(start_reg_name) and not(VIVADO.REG_NAME_IS_OUTPUT_REG(end_reg_name)):
		print "	Comb path from input register in top.vhd to pipeline logic"
		start_stage = 0
	elif not(VIVADO.REG_NAME_IS_INPUT_REG(start_reg_name)) and VIVADO.REG_NAME_IS_OUTPUT_REG(end_reg_name):
		print "	Comb path from pipeline logic to output register in top.vhd"
		end_stage = timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)
	else:
		print "	Comb path somewhere in the pipeline logic"
		
	print "	Taking register merging into account and assuming largest search range based on earliest when used and latest when complete times"
	found_start_stage = None
	if not(VIVADO.REG_NAME_IS_INPUT_REG(start_reg_name)):
		# all possible reg paths considering renaming
		# Start names
		start_names = [start_reg_name]
		start_aliases = []
		if start_reg_name in parsed_timing_report.reg_merged_with:
			start_aliases = parsed_timing_report.reg_merged_with[start_reg_name]
		start_names += start_aliases
		
		start_stages_when_used = []
		for start_name in start_names:
			start_inst = VIVADO.GET_MOST_MATCHING_LOGIC_INST_FROM_REG_NAME(start_name, logic, LogicLookupTable)
			when_used = GET_ABS_SUBMODULE_STAGE_WHEN_USED(start_inst, logic, parser_state, TimingParamsLookupTable)
			print "		",start_name
			print "			",start_inst,"used in absolute stage number:",when_used
			start_stages_when_used.append(when_used)
		found_start_stage = min(start_stages_when_used)
	
	found_end_stage = None
	if not(VIVADO.REG_NAME_IS_OUTPUT_REG(end_reg_name)):
		# all possible reg paths considering renaming
		# End names
		end_names = [end_reg_name]
		end_aliases = []
		if end_reg_name in parsed_timing_report.reg_merged_with:
			end_aliases = parsed_timing_report.reg_merged_with[end_reg_name]
		end_names += end_aliases

		end_stages_when_complete = []
		for end_name in end_names:
			end_inst = VIVADO.GET_MOST_MATCHING_LOGIC_INST_FROM_REG_NAME(end_name, logic, LogicLookupTable)
			when_complete = GET_ABS_SUBMODULE_STAGE_WHEN_COMPLETE(end_inst, logic, LogicLookupTable, TimingParamsLookupTable)
			print "		",end_name
			print "			",end_inst,"completes in absolute stage number:",when_complete
			end_stages_when_complete.append(when_complete)
		found_end_stage = max(end_stages_when_complete)
	
	if not(found_start_stage is None) and (found_start_stage < start_stage):
		start_stage = found_start_stage
	if not(found_end_stage is None) and (found_end_stage > end_stage):
		end_stage = found_end_stage	
	
	# Adjust -1/+1 for start to include comb logic from previous stage
	if start_stage > 0:
		start_stage -= 1
	if end_stage < (timing_params.GET_TOTAL_LATENCY(parser_state, TimingParamsLookupTable)-1):
		end_stage += 1
		
	return start_stage, end_stage




def GET_RAW_HDL_PIPELINE_MAP(logic, TimingParamsLookupTable, LogicLookupTable):
	# Get lls per stage est
	submodule_lls_per_stage = GET_SUBMODULE_EST_LLS_PER_STAGE_DICT(TimingParamsLookupTable, LogicLookupTable)
	
	# Try to use cached pipeline map
	cached_pipeline_map = GET_CACHED_PIPELINE_MAP(logic, TimingParamsLookupTable, LogicLookupTable)
	raw_hdl_lls_pipeline_map = None
	if not(cached_pipeline_map is None):
		print "Using cached raw VHDL pipeline stages and submodule levels lookup (pipeline map)..."
		raw_hdl_lls_pipeline_map = cached_pipeline_map
	else:
		print "Building raw VHDL pipeline stages and submodule levels lookup (pipeline map)..."
		# Looks LLs per stage
		raw_hdl_lls_pipeline_map = VHDL.RECURSIVE_GET_RAW_HDL_LOGIC_LEVELS_PIPELINE_MAP(logic, LogicLookupTable, TimingParamsLookupTable, submodule_lls_per_stage)
		
	# Write raw hdl cache
	WRITE_PIPELINE_MAP_CACHE_FILE(logic, raw_hdl_lls_pipeline_map, TimingParamsLookupTable, parser_state)
	
	return raw_hdl_lls_pipeline_map

# Searches pipeline for raw hdl submodule that will split the stage with most LLs stage
def GET_WORST_PATH_RAW_HDL_SUBMODULE_INST_FROM_STAGE_END_STAGES(logic, start_stage, end_stage, LogicLookupTable, TimingParamsLookupTable, parsed_timing_report):
	# Get the pipeline map
	raw_hdl_lls_pipeline_map = GET_RAW_HDL_PIPELINE_MAP(logic, TimingParamsLookupTable, LogicLookupTable)
	
	#for stage in raw_hdl_lls_pipeline_map:
	#	print "STAGE", stage,"==========================="
	#	for ll in raw_hdl_lls_pipeline_map[stage]:
	#		print "LL",ll, raw_hdl_lls_pipeline_map[stage][ll]	
	
	# Find stage with max sequential logic levels in search range
	max_lls_offset_per_stage = 0
	max_lls_stage = None
	for stage in raw_hdl_lls_pipeline_map:
		if (stage >= start_stage) and (stage <= end_stage):
			lls_dict = raw_hdl_lls_pipeline_map[stage]
			max_lls_offset_this_stage = 0
			for ll_offset in lls_dict:
				if ll_offset >= max_lls_offset_this_stage:
					max_lls_offset_this_stage = ll_offset
			print "	Stage",stage,"LLs:", max_lls_offset_this_stage

			# Is this max the overall max?				
			if max_lls_offset_this_stage >= max_lls_offset_per_stage:
				max_lls_offset_per_stage = max_lls_offset_this_stage
				max_lls_stage = stage
					
	print "Stage", max_lls_stage,"has max estimated lls @", max_lls_offset_per_stage, "LLs"
	
	
	# Split stage in half 
	max_lls_stage_lls_dict = raw_hdl_lls_pipeline_map[max_lls_stage]
	mid_stage_lls_offset = int(float(max_lls_offset_per_stage)/ 2.0)
	#print "mid_stage_lls_offset pre round", mid_stage_lls_offset
	# Round to nearest lls offset in dictionary
	dist = 0
	while not(mid_stage_lls_offset in max_lls_stage_lls_dict):
		left_offset = mid_stage_lls_offset - dist
		right_offset = mid_stage_lls_offset + dist
		
		if left_offset in max_lls_stage_lls_dict:
			mid_stage_lls_offset = left_offset
			break
		if right_offset in max_lls_stage_lls_dict:
			mid_stage_lls_offset = right_offset
			break
	print "	Logic level offset", mid_stage_lls_offset,"is mid stage"
	
	# Loop over mid stage and find module with max lls per stage
	max_lls_per_stage = 0
	max_lls_per_stage_inst = None
	parallel_mid_stage_sub_insts = max_lls_stage_lls_dict[mid_stage_lls_offset]
	submodule_lls_per_stage = GET_SUBMODULE_EST_LLS_PER_STAGE_DICT(TimingParamsLookupTable, LogicLookupTable)
	for parallel_mid_stage_sub_inst in parallel_mid_stage_sub_insts:
		slice_per_stage = submodule_lls_per_stage[parallel_mid_stage_sub_inst]
		if slice_per_stage >= max_lls_per_stage:
			max_lls_per_stage = slice_per_stage
			max_lls_per_stage_inst = parallel_mid_stage_sub_inst
			
	print "	Most LLs per stage in at that logic level offset:"
	print "		", max_lls_per_stage_inst
	
	return max_lls_per_stage_inst	
	
	

	

