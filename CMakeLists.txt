cmake_minimum_required(VERSION 3.20)
project(mag_stack VERSION 2.0.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Threads REQUIRED)
find_package(OpenSSL REQUIRED)
find_package(CURL REQUIRED)

# What a bot finds as ~/MAG_CONTEXT.md in its VM: one versioned markdown,
# embedded into magbot at build time.
set(MAG_BOT_CONTEXT_MD ${CMAKE_CURRENT_SOURCE_DIR}/mag-room/native/bot_context.md)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${MAG_BOT_CONTEXT_MD})
file(READ ${MAG_BOT_CONTEXT_MD} MAG_BOT_CONTEXT)
file(CONFIGURE OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/generated/bot_context.hpp
     CONTENT "#pragma once\n// Generated from mag-room/native/bot_context.md; do not edit.\nstatic const char *const BOT_CONTEXT_MD = R\"MAGCTX(@MAG_BOT_CONTEXT@)MAGCTX\";\n" @ONLY)
include_directories(${CMAKE_CURRENT_BINARY_DIR}/generated)

set(WARN -Wall -Wextra -Wpedantic)

# The room: HTTP API, event stream, conversation engine, tools.
add_executable(magroom mag-room/native/magroom.cpp)
target_compile_options(magroom PRIVATE ${WARN})
target_link_libraries(magroom PRIVATE Threads::Threads OpenSSL::Crypto CURL::libcurl icuuc)

# Model supervisor: download watch, verification and start of the two
# CPU-only llama.cpp servers (ops/models).
add_executable(mag-modeld mag-room/native/mag_modeld.cpp)
target_compile_options(mag-modeld PRIVATE ${WARN})
target_link_libraries(mag-modeld PRIVATE CURL::libcurl OpenSSL::Crypto Threads::Threads icuuc)

# Audit gateway: the only path from the room to the model servers.
add_executable(mag-audit-gateway mag-room/native/mag_audit_gateway.cpp)
target_compile_options(mag-audit-gateway PRIVATE ${WARN})
target_link_libraries(mag-audit-gateway PRIVATE Threads::Threads OpenSSL::Crypto icuuc)

# VM lifecycle and guest exec (installed as /usr/local/bin/magbot).
add_executable(magbot-native mag-room/native/vm.cpp)
target_compile_options(magbot-native PRIVATE ${WARN})
target_link_libraries(magbot-native PRIVATE Threads::Threads icuuc)

# Browser controller that runs inside each bot VM.
add_executable(mag-browser mag-room/native/browser.cpp)
target_compile_options(mag-browser PRIVATE ${WARN})
target_link_libraries(mag-browser PRIVATE Threads::Threads OpenSSL::Crypto icuuc util)

# Per-VM static site publisher (libvirt bridge only).
add_executable(mag-bots-publish ops/bots/publishd.cpp)
target_compile_options(mag-bots-publish PRIVATE ${WARN})

# Speech to text for the room (ops/stt). The recognizer is transcribe.cpp,
# built outside this tree by ops/stt/bootstrap.sh; without it (CI) the daemon
# still builds and runs its protocol on a fake engine.
add_executable(mag-stt mag-room/native/mag_stt.cpp)
target_compile_options(mag-stt PRIVATE ${WARN})
target_link_libraries(mag-stt PRIVATE Threads::Threads OpenSSL::Crypto CURL::libcurl icuuc)
set(MAG_TRANSCRIBE_SRC "" CACHE PATH "transcribe.cpp checkout (ops/stt/bootstrap.sh)")
set(MAG_TRANSCRIBE_BUILD "" CACHE PATH "its CMake build directory")
if(MAG_TRANSCRIBE_SRC AND MAG_TRANSCRIBE_BUILD)
  target_compile_definitions(mag-stt PRIVATE MAG_STT_TRANSCRIBE=1)
  target_include_directories(mag-stt PRIVATE ${MAG_TRANSCRIBE_SRC}/include)
  target_link_libraries(mag-stt PRIVATE
    ${MAG_TRANSCRIBE_BUILD}/src/libtranscribe.a
    ${MAG_TRANSCRIBE_BUILD}/ggml/src/libggml.a
    ${MAG_TRANSCRIBE_BUILD}/ggml/src/libggml-cpu.a
    ${MAG_TRANSCRIBE_BUILD}/ggml/src/libggml-base.a
    ${CMAKE_DL_LIBS} m)
endif()

add_executable(test-group-dispatcher mag-room/tests/test_group_dispatcher.cpp)

install(TARGETS magroom mag-browser magbot-native mag-audit-gateway mag-bots-publish mag-modeld RUNTIME DESTINATION bin)
install(DIRECTORY mag-room/web/ DESTINATION web)

# Samsung S23 control daemon (devices/s23); independent of the room.
add_executable(s23ctl devices/s23/s23ctl.cpp)
target_compile_options(s23ctl PRIVATE ${WARN})
target_link_libraries(s23ctl PRIVATE Threads::Threads OpenSSL::Crypto)
