# =============================================================================
# Theme Park World - static recompilation host
#
#   python3 run_lift.py --all     # -> src/recomp/gen/ (needs work/tp/functions.json)
#   ./build-macos.sh              # -> build-macos/tpw
#   build-macos/tpw --run
# =============================================================================
cmake_minimum_required(VERSION 3.20)
project(tpw C)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()
if(NOT (APPLE OR CMAKE_SYSTEM_NAME STREQUAL "Linux"))
  message(FATAL_ERROR "macOS first, Linux next; there is no Windows host yet")
endif()

set(PCRECOMP "${CMAKE_SOURCE_DIR}/../pcrecomp" CACHE PATH "pcrecomp checkout")
set(GEN_DIR src/recomp/gen)
file(GLOB GEN_SOURCES CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/${GEN_DIR}/*.c")
if(NOT GEN_SOURCES)
  message(FATAL_ERROR "${GEN_DIR} is empty -- run python3 run_lift.py --all first (docs/building.md)")
endif()

# The lifted C on runtime/win32hle. 64-bit (macOS) the guest's 4 GB is one
# block of the host's memory; 32-bit Linux a guest address is a host address.
find_package(PkgConfig REQUIRED)
pkg_check_modules(SDL2 REQUIRED IMPORTED_TARGET sdl2)
pkg_check_modules(SDL2_TTF REQUIRED IMPORTED_TARGET SDL2_ttf)
set(HLE ${PCRECOMP}/runtime/win32hle)
file(GLOB HLE_SOURCES CONFIGURE_DEPENDS "${HLE}/*.c")
list(FILTER HLE_SOURCES EXCLUDE REGEX "_selftest\\.c$")

add_executable(tpw src/host/main.c src/host/cpu.c src/host/script.c src/host/shims.c ${HLE_SOURCES} ${GEN_SOURCES})
target_include_directories(tpw PRIVATE src/host ${GEN_DIR} ${HLE} ${PCRECOMP}/runtime/recomp32)
target_compile_definitions(tpw PRIVATE RECOMP_RETADDR=0u RECOMP_LOCAL_REGS)
if(CMAKE_SIZEOF_VOID_P EQUAL 4)
  target_compile_definitions(tpw PRIVATE RECOMP_FLAT_MEMORY)
else()
  # A fixed address lets the lifted code add a constant, not a global it
  # reloads after every store. On a Mac 16 TB up is free on arm64 and x86-64.
  if(APPLE)
    set(TPW_MEM_BASE "0x100000000000ull" CACHE STRING "host address of the guest's 4 GB (empty: anywhere)")
  else()
    set(TPW_MEM_BASE "" CACHE STRING "host address of the guest's 4 GB (empty: anywhere)")
  endif()
  if(TPW_MEM_BASE)
    target_compile_definitions(tpw PRIVATE RECOMP_MEM_BASE=${TPW_MEM_BASE})
  endif()
endif()
set(GEN_OPT "-O2" CACHE STRING "optimisation for the generated C")
set_source_files_properties(${GEN_SOURCES} PROPERTIES COMPILE_OPTIONS "-w;${GEN_OPT};-fno-strict-aliasing;-fwrapv")
target_compile_options(tpw PRIVATE -fno-strict-aliasing -fwrapv)
target_link_libraries(tpw PRIVATE PkgConfig::SDL2 PkgConfig::SDL2_TTF pthread m)

# A function-entry trace for bring-up (src/host/trace.c: TPW_FIRSTHIT and the
# last entries at exit). Every lifted function pays for it, so it gets its own
# build folder: BUILD_DIR=build-macos-trace CMAKE_ARGS=-DTPW_TRACE=ON
option(TPW_TRACE "record lifted function entries" OFF)
if(TPW_TRACE)
  target_sources(tpw PRIVATE src/host/trace.c)
  target_compile_definitions(tpw PRIVATE RECOMP_TRACE=1)
endif()
