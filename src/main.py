#!/usr/bin/env python3
# -*- coding: utf-8 -*- 

import sys
sys.dont_write_bytecode = True

import C_TO_LOGIC
import SYN
import MODELSIM

print('''
██████╗ ██╗██████╗ ███████╗██╗     ██╗███╗   ██╗███████╗ ██████╗
██╔══██╗██║██╔══██╗██╔════╝██║     ██║████╗  ██║██╔════╝██╔════╝
██████╔╝██║██████╔╝█████╗  ██║     ██║██╔██╗ ██║█████╗  ██║     
██╔═══╝ ██║██╔═══╝ ██╔══╝  ██║     ██║██║╚██╗██║██╔══╝  ██║     
██║     ██║██║     ███████╗███████╗██║██║ ╚████║███████╗╚██████╗
╚═╝     ╚═╝╚═╝     ╚══════╝╚══════╝╚═╝╚═╝  ╚═══╝╚══════╝ ╚═════╝
''')

# No need to scare folks with my big dumb todo list printing...
'''
TODO:
Come on do sanity check for reserved words
Make a chipscope+virtual io debug thing, w/ pass through regular streaming uart io?
^Full board arty example using ddr, sw,leds, uart for debug+stream, etc
Make MAIN_MHZ allow not yet defined funcs for mhz values later
Continue completing clock crossing features, tool support (volatile/pipelined logic wires w/ clock enable double check)
Detect/document function types ...ex. Detect single instance / NO-CONDTIONAL|clock en funcs and record logic.is_single_inst, maybe need to add pragmas to specify/force
Rust AST? rustc -Z ast-json, traverse the json?
Document pragmas
Reorg global logic detection/rounding from bottom up instead of trying multiple times to slice from top down.
Do a simple RISCV? cpu? But avoid just doing RTL clone?
Fix bug of not being able to include auto gen headers in auto gend files
Static locals inside {} with #pragma global or something? infer state reg submodule ports into and out of {}, etc
Fix fine grain sweep to work again
Redo and examine pipelining of basic c ops n cycle G/LT/E is known problem - write in PipelineC?
^ Maybe redo pipelined binary trees instead of equal bit sequential stages? Bad for slicing.. probably can work, test
Gen flattened slv top level ports, w/ internal type conversions -W.
Check for non global functions that call global functions when evaluating const
^ Dont store timing params for zero clocks - no timing params assumed default, save ram, probably faster might not need to do below.
Parallelize parsing of func def bodies since indepdent of other func def bodies?
Reorg ref tok cover checking and removal (collapsing,etc too?) into tree based data struct
Do hacky if generate with pragmas? oh gosh
Look into intermediate representation such FIRRTL (what Yosys uses?) instead, eventually get rid of VHDL...
OPTIMIZE AWAY CONSTANTs: do as easy c ast manip? mult by 1 or neg 1, mult by 2^n and div by 2^n, (floats and ints!) I dont want to be a compiler
Cache/return once true/reorganize into timing params LOGIC_NEEDS_ code?
Solve AWS multithreaded off by one or two problem...
Support AWS PCIM port - need to write kernel driver and maybe user space wrapper?
Add constant defintion (struct+array init), use const keyword? #define init expressions? Init becomes like const ref funcs assigning ref toks
How to do module instantiation? Does that need to be macro based? #define to set 'generics'?
Relative/'local' clock crossings - not absolute specified in main/pragmas? Can point at any non-single-inst function to run in requested relative clock...?
Fix for vhdl restricted words. Append _restricted?
Do auto gen unsigned to array functions now that support array_N_t
Use gcc array init instead of for loop many const ref tok assignments that are bulky?
Add timing info via syn top level without flatten, then report timing on each module? No IO regs so will paths make sense? / doesnt work for combinatorial logic? Hah syn weird top level with _top version of everything?
When doing const ref read with many ref toks, make new 'assignment' alias of new reduced wire so future reads of the same const ref can use the single wire
Yo dummy dont make built in operations have resize() on outputs, output determined by inputs only
Add look ahead for built in functions so cast can be inferred
Remove RESOLVE_CONST_ARRAY_REF from C_AST_REF_TO_TOKENS, and max var ref / var assignement optimize to const ref and const assignment... complicated...
Consider doing constant optimization as second pass (faster than current way of optimizing as part of first pass?)? How to unroll const for-loop then?
Pragmas for reset stuff, tag global regs for reset, special clock domain handling stuff with PLL lock and such?
Allow mix of volatile and non-volatile globals by isolating logic using globals (as is done now by putting in other global func)?
Maybe can implement variable time loops as PipelineC state machines?? Weird idea Andrew
CANNOT PROPOGATE CONSTANTS through compound references (structs, arrays)
Try big const ref funcs (not vhdl expr) in modules instead of all in one file where used? removes repeated code for faster elab?
Built in raw vhdl funcs for array copy / manipulation instead of many const rek tok loops. Built in funcs can return arrays (handled internally) but user can write such funcs  uint8*4_t[N/4] = uint8_arrayN_by_4_le(uint8_t x[N])
Multiple reads on globals from pipelined logic is maybe OK? ordering of global writes and reads a problem?, do at some point...
Auto gen <func_name>_t type which is inputs to function func(x,y,z)  struct <func_name>_t {x,y,z}
Syn each pipeline stage ... this is hard... like slicing zero clock logic 
Uh ceil log2 stuff doesnt work for huge consts determining bit width in python? 0x800000000000008b
Const SR/SL as vhdl funcs instead of modules..thought this was done...
Add Quartus fine grain sweep support and better generated final files
Progate constants through constant bit manip, ex. uint32_uint1_31
Redo old code to use for loops instead of generated code (ex. float div)
Document fundamental time v.s space trade off (sum example?)
Better command line name? - 'pipelinec'? ...
'''

print("================== Parsing C Code to Logical Hierarchy ================================")
c_file = "main.c"
if len(sys.argv) == 2:
  # Assume first and only arg is c_file
  c_file = sys.argv[1]
parser_state = C_TO_LOGIC.PARSE_FILE(c_file)

print("================== Adding Timing Information from Synthesis Tool ================================")
parser_state = SYN.ADD_PATH_DELAY_TO_LOOKUP(parser_state)

print("================== Doing Optional Modelsim Debug ================================")
MODELSIM.DO_OPTIONAL_DEBUG(False)

print("================== Beginning Throughput Sweep ================================")
multimain_timing_params = SYN.DO_THROUGHPUT_SWEEP(parser_state)

print("================== Writing Results of Throughput Sweep ================================")
SYN.WRITE_FINAL_FILES(multimain_timing_params, parser_state)
print("Done.")







