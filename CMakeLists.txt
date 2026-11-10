# =============================================================================
# Theme Park World - static recompilation host
#
#   python3 run_lift.py --all     # -> src/recomp/gen/ (needs work/tp/functions.json)
#   ./build-macos.sh              # -> build-macos/tpw
#   build-macos/tpw --run
# =============================================================================
cmake_minimum_required(VERSION 3.20)
project(tpw C)
if(APPLE)
  enable_language(OBJC)
endif()

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()
if(NOT (APPLE OR CMAKE_SYSTEM_NAME STREQUAL "Linux"))
  message(FATAL_ERROR "macOS first, Linux next; there is no Windows host yet")
endif()

set(PCRECOMP "${CMAKE_SOURCE_DIR}/../pcrecomp" CACHE PATH "pcrecomp checkout")
set(GEN_DIR "${CMAKE_SOURCE_DIR}/src/recomp/gen" CACHE PATH "the lifted C (run_lift.py --out)")
file(GLOB GEN_SOURCES CONFIGURE_DEPENDS "${GEN_DIR}/*.c")
if(NOT GEN_SOURCES)
  message(FATAL_ERROR "${GEN_DIR} is empty -- run python3 run_lift.py --all first (docs/building.md)")
endif()

# The lifted C on runtime/win32hle. 64-bit (macOS) the guest's 4 GB is one
# block of the host's memory; 32-bit Linux a guest address is a host address.
# SDL2 and SDL2_ttf from pkg-config (Homebrew), or as the frameworks
# Theme Park World.app carries (app/toolchain.sh): TPW_SDL_FRAMEWORKS names
# their folder. The sources include <SDL2/SDL.h> and <SDL2/SDL_ttf.h>, so both
# frameworks' headers are gathered into one SDL2 folder.
set(TPW_SDL_FRAMEWORKS "" CACHE PATH "a folder with SDL2.framework and SDL2_ttf.framework (empty: pkg-config)")
if(TPW_SDL_FRAMEWORKS)
  set(SDL_INCLUDE ${CMAKE_BINARY_DIR}/sdl-include)
  file(REMOVE_RECURSE ${SDL_INCLUDE})
  file(GLOB SDL_HEADERS ${TPW_SDL_FRAMEWORKS}/SDL2.framework/Headers/*.h ${TPW_SDL_FRAMEWORKS}/SDL2_ttf.framework/Headers/*.h)
  file(COPY ${SDL_HEADERS} DESTINATION ${SDL_INCLUDE}/SDL2)
  add_library(tpw_sdl INTERFACE)
  target_include_directories(tpw_sdl INTERFACE ${SDL_INCLUDE} ${SDL_INCLUDE}/SDL2)
  # Found as libraries, so CMake writes the -F and -framework flags itself,
  # quoted: the folder is in Application Support, a path with spaces.
  find_library(TPW_SDL2 SDL2 PATHS ${TPW_SDL_FRAMEWORKS} NO_DEFAULT_PATH REQUIRED)
  find_library(TPW_SDL2_TTF SDL2_ttf PATHS ${TPW_SDL_FRAMEWORKS} NO_DEFAULT_PATH REQUIRED)
  target_link_libraries(tpw_sdl INTERFACE ${TPW_SDL2} ${TPW_SDL2_TTF})
else()
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(SDL2 REQUIRED IMPORTED_TARGET sdl2)
  pkg_check_modules(SDL2_TTF REQUIRED IMPORTED_TARGET SDL2_ttf)
  add_library(tpw_sdl INTERFACE)
  target_link_libraries(tpw_sdl INTERFACE PkgConfig::SDL2 PkgConfig::SDL2_TTF)
endif()
set(HLE ${PCRECOMP}/runtime/win32hle)
file(GLOB HLE_SOURCES CONFIGURE_DEPENDS "${HLE}/*.c")
list(FILTER HLE_SOURCES EXCLUDE REGEX "_selftest\\.c$")

# Compiled once, linked twice: tpw, the program, and libtpw.dylib, the same
# game for Theme Park World.app to load (app/launcher.m), entered at tpw_main.
add_library(tpw_obj OBJECT src/host/main.c src/host/cpu.c src/host/qmixer.c src/host/script.c src/host/shims.c src/host/widescreen.c ${HLE_SOURCES} ${GEN_SOURCES})
set_target_properties(tpw_obj PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(tpw_obj PRIVATE src/host ${GEN_DIR} ${HLE} ${PCRECOMP}/runtime/recomp32)
# RECOMP_X87_WINDOW: the lifted x87 stack's push and pop move a pointer rather
# than all eight registers (pcrecomp's recomp_types.h); the game's lighting
# and transforms are full of them.
target_compile_definitions(tpw_obj PRIVATE RECOMP_RETADDR=0u RECOMP_LOCAL_REGS RECOMP_X87_WINDOW)
if(CMAKE_SIZEOF_VOID_P EQUAL 4)
  target_compile_definitions(tpw_obj PRIVATE RECOMP_FLAT_MEMORY)
else()
  # A fixed address lets the lifted code add a constant, not a global it
  # reloads after every store. On a Mac 16 TB up is free on arm64 and x86-64.
  if(APPLE)
    set(TPW_MEM_BASE "0x100000000000ull" CACHE STRING "host address of the guest's 4 GB (empty: anywhere)")
  else()
    set(TPW_MEM_BASE "" CACHE STRING "host address of the guest's 4 GB (empty: anywhere)")
  endif()
  if(TPW_MEM_BASE)
    target_compile_definitions(tpw_obj PRIVATE RECOMP_MEM_BASE=${TPW_MEM_BASE})
  endif()
endif()
set(GEN_OPT "-O2" CACHE STRING "optimisation for the generated C")
set_source_files_properties(${GEN_SOURCES} PROPERTIES COMPILE_OPTIONS "-w;${GEN_OPT};-fno-strict-aliasing;-fwrapv")
target_compile_options(tpw_obj PRIVATE -fno-strict-aliasing -fwrapv)
target_link_libraries(tpw_obj PUBLIC tpw_sdl pthread m ${CMAKE_DL_LIBS})   # dl: the Vulkan loader, opened at run time
# Direct3D on the GPU: Metal (--renderer metal), without ARC (d3d_metal.m).
if(APPLE)
  target_sources(tpw_obj PRIVATE ${HLE}/d3d_metal.m)
  set_source_files_properties(${HLE}/d3d_metal.m PROPERTIES COMPILE_OPTIONS "-fno-objc-arc")
  target_link_libraries(tpw_obj PUBLIC "-framework Metal" "-framework Foundation")
endif()

# A function-entry trace for bring-up (src/host/trace.c: TPW_FIRSTHIT and the
# last entries at exit). Every lifted function pays for it, so it gets its own
# build folder: BUILD_DIR=build-macos-trace CMAKE_ARGS=-DTPW_TRACE=ON
option(TPW_TRACE "record lifted function entries" OFF)
if(TPW_TRACE)
  target_sources(tpw_obj PRIVATE src/host/trace.c)
  target_compile_definitions(tpw_obj PRIVATE RECOMP_TRACE=1)
endif()

add_executable(tpw src/host/exe.c)
target_link_libraries(tpw PRIVATE tpw_obj)
if(TPW_SDL_FRAMEWORKS)
  set_target_properties(tpw PROPERTIES BUILD_RPATH ${TPW_SDL_FRAMEWORKS})
endif()
add_library(tpw_game SHARED)
set_target_properties(tpw_game PROPERTIES OUTPUT_NAME tpw)
target_link_libraries(tpw_game PRIVATE tpw_obj)
