import sys
import os

import SYN
import EDAPLAY
import MODELSIM
import OPEN_TOOLS
import CXXRTL

# Default simulation tool is free edaplayground
SIM_TOOL = EDAPLAY

def SET_SIM_TOOL(cmd_line_args):
  global SIM_TOOL
  if cmd_line_args.edaplay:
    SIM_TOOL = EDAPLAY
  elif cmd_line_args.cxxrtl:
    SIM_TOOL = CXXRTL
  elif cmd_line_args.modelsim:
    SIM_TOOL = MODELSIM

def DO_OPTIONAL_SIM(do_sim, parser_state, latency=0):
  if SIM_TOOL is EDAPLAY:
    if do_sim:
      EDAPLAY.SETUP_EDAPLAY(latency)
  elif SIM_TOOL is MODELSIM:
    # Assume modelsim is default sim
    MODELSIM.DO_OPTIONAL_DEBUG(do_sim, latency)
  elif SIM_TOOL is CXXRTL:
    if do_sim:
      # Assume modelsim is default sim
      CXXRTL.DO_SIM(latency, parser_state)
  else:
    print("Unknown simulation tool:",SIM_TOOL)

def GET_SIM_FILES(latency=0):
  # Get all vhd files in syn output
  vhd_files = []
  for root, dirs, filenames in os.walk(SYN.SYN_OUTPUT_DIRECTORY):
    for f in filenames:
      if f.endswith(".vhd"):
        #print "f",f
        fullpath = os.path.join(root, f)
        abs_path = os.path.abspath(fullpath)
        #print "fullpath",fullpath
        #f
        if ( (root == SYN.SYN_OUTPUT_DIRECTORY + "/main") and
           f.startswith("main_") and f.endswith("CLK.vhd") ):
          clks = int(f.replace("main_","").replace("CLK.vhd",""))
          if clks==latency:
            print("Using top:",abs_path)
            vhd_files.append(abs_path)
        else:
          vhd_files.append(abs_path)
          
  return vhd_files
