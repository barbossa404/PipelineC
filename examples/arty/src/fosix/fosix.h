// POSIX-like interfaces for FPGA...FOSIX?
// System calls requests are made from a process to the system
// System calls responses are from a the system to a process

// Types, constants, etc for fosix system calls
#include "uintN_t.h"
#include "intN_t.h"

#pragma once

// Syscall table
#define syscall_t uint8_t
#define FOSIX_READ  0
#define FOSIX_WRITE 1
#define FOSIX_OPEN  2
#define FOSIX_CLOSE 3
#define FOSIX_UNKNOWN 255

// Exchanging the small/est possible messages / buffer sizes
// syscall id byte
// S syscall bytes
// Path is really largest requirement
#define fosix_fd_t int32_t
#define fosix_size_t uint32_t
#define FOSIX_BUF_SIZE 32
#define FOSIX_LOG2_BUF_SIZE 5
#define FOSIX_PATH_SIZE 32

typedef struct open_req_t
{
	char path[FOSIX_PATH_SIZE];
	uint1_t valid;
} open_req_t;
open_req_t OPEN_REQ_T_NULL()
{
  open_req_t rv;
  fosix_size_t i;
  for(i=0;i<FOSIX_PATH_SIZE;i=i+1)
  {
    rv.path[i] = 0;
  }
  rv.valid = 0;
  return rv;
}

typedef struct open_proc_to_sys_t
{
	open_req_t req;
	uint1_t resp_ready;
} open_proc_to_sys_t;
open_proc_to_sys_t OPEN_PROC_TO_SYS_T_NULL()
{
  open_proc_to_sys_t rv;
  rv.req = OPEN_REQ_T_NULL();
  rv.resp_ready = 0;
  return rv;
}

typedef struct open_resp_t
{
	fosix_fd_t fildes;
  uint1_t valid;
} open_resp_t;
open_resp_t OPEN_RESP_T_NULL()
{
  open_resp_t rv;
  rv.fildes = -1;
  rv.valid = 0;
  return rv;
}

typedef struct open_sys_to_proc_t
{
	open_resp_t resp;
	uint1_t req_ready;
} open_sys_to_proc_t;
open_sys_to_proc_t OPEN_SYS_TO_PROC_T_NULL()
{
  open_sys_to_proc_t rv;
  rv.resp = OPEN_RESP_T_NULL();
  rv.req_ready = 0;
  return rv;
}

typedef struct write_req_t
{
	fosix_fd_t fildes;
	uint8_t buf[FOSIX_BUF_SIZE];
	fosix_size_t nbyte;
	uint1_t valid;
} write_req_t;
write_req_t WRITE_REQ_T_NULL()
{
  write_req_t rv;
  rv.fildes = -1;
  fosix_size_t i;
  for(i=0;i<FOSIX_BUF_SIZE;i=i+1)
  {
    rv.buf[i] = 0;
  }
  rv.nbyte = 0;
  rv.valid = 0;
  return rv;
}

typedef struct write_proc_to_sys_t
{
	write_req_t req;
	uint1_t resp_ready;
} write_proc_to_sys_t;
write_proc_to_sys_t WRITE_PROC_TO_SYS_T_NULL()
{
  write_proc_to_sys_t rv;
  rv.req = WRITE_REQ_T_NULL();
  rv.resp_ready = 0;
  return rv;
}

typedef struct write_resp_t
{
	fosix_size_t nbyte;
	uint1_t valid;
} write_resp_t;
write_resp_t WRITE_RESP_T_NULL()
{
  write_resp_t rv;
  rv.nbyte = 0;
  rv.valid = 0;
  return rv;
}

typedef struct write_sys_to_proc_t
{
  write_resp_t resp;
	uint1_t req_ready;
} write_sys_to_proc_t;
write_sys_to_proc_t WRITE_SYS_TO_PROC_T_NULL()
{
  write_sys_to_proc_t rv;
  rv.resp = WRITE_RESP_T_NULL();
  rv.req_ready = 0;
  return rv;
}

typedef struct read_req_t
{
	fosix_fd_t fildes;
	fosix_size_t nbyte;
	uint1_t valid;
} read_req_t;
read_req_t READ_REQ_T_NULL()
{
  read_req_t rv;
  rv.fildes = -1;
  rv.nbyte = 0;
  rv.valid = 0;
  return rv;
}

typedef struct read_proc_to_sys_t
{
	read_req_t req;
	uint1_t resp_ready;
} read_proc_to_sys_t;
read_proc_to_sys_t READ_PROC_TO_SYS_T_NULL()
{
  read_proc_to_sys_t rv;
  rv.req = READ_REQ_T_NULL();
  rv.resp_ready = 0;
  return rv;
}

typedef struct read_resp_t
{
	fosix_size_t nbyte;
  uint8_t buf[FOSIX_BUF_SIZE];
	uint1_t valid;
} read_resp_t;
read_resp_t READ_RESP_T_NULL()
{
  read_resp_t rv;
  rv.nbyte = 0;
  fosix_size_t i;
  for(i=0;i<FOSIX_BUF_SIZE;i=i+1)
  {
    rv.buf[i] = 0;
  }
  rv.valid = 0;
  return rv;
}

typedef struct read_sys_to_proc_t
{
  read_resp_t resp;
	uint1_t req_ready;
} read_sys_to_proc_t;
read_sys_to_proc_t READ_SYS_TO_PROC_T_NULL()
{
  read_sys_to_proc_t rv;
  rv.resp = READ_RESP_T_NULL();
  rv.req_ready = 0;
  return rv;
}

typedef struct close_req_t
{
	fosix_fd_t fildes;
	uint1_t valid;
} close_req_t;
close_req_t CLOSE_REQ_T_NULL()
{
  close_req_t rv;
  rv.fildes = -1;
  rv.valid = 0;
  return rv;
}

typedef struct close_proc_to_sys_t
{
	close_req_t req;
	uint1_t resp_ready;
} close_proc_to_sys_t;
close_proc_to_sys_t CLOSE_PROC_TO_SYS_T_NULL()
{
  close_proc_to_sys_t rv;
  rv.req = CLOSE_REQ_T_NULL();
  rv.resp_ready = 0;
  return rv;
}

typedef struct close_resp_t
{
	int32_t err;
  uint1_t valid;
} close_resp_t;
close_resp_t CLOSE_RESP_T_NULL()
{
  close_resp_t rv;
  rv.err = 0;
  rv.valid = 0;
  return rv;
}

typedef struct close_sys_to_proc_t
{
	close_resp_t resp;
	uint1_t req_ready;
} close_sys_to_proc_t;
close_sys_to_proc_t CLOSE_SYS_TO_PROC_T_NULL()
{
  close_sys_to_proc_t rv;
  rv.resp = CLOSE_RESP_T_NULL();
  rv.req_ready = 0;
  return rv;
}
