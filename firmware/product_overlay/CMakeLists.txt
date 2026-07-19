if("$ENV{STACKCHAN_GENERATED_CONFIG_DIR}" STREQUAL "")
    message(FATAL_ERROR "STACKCHAN_GENERATED_CONFIG_DIR is required for a product build")
endif()

set(STACKCHAN_GENERATED_CONFIG_DIR "$ENV{STACKCHAN_GENERATED_CONFIG_DIR}")

if(NOT EXISTS "${STACKCHAN_GENERATED_CONFIG_DIR}/device_auth_config.h")
    message(FATAL_ERROR "Generated device_auth_config.h was not found")
endif()

idf_component_register(
    SRCS "secret_logic_override.cpp"
    INCLUDE_DIRS "${STACKCHAN_GENERATED_CONFIG_DIR}"
    WHOLE_ARCHIVE
)
