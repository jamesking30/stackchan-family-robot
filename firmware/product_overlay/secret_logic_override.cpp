#include <string>
#include <string_view>

#include "device_auth_config.h"

namespace secret_logic {

std::string generate_auth_token()
{
    return STACKCHAN_DEVICE_TOKEN;
}

std::string generate_handshake_token(std::string_view)
{
    return STACKCHAN_DEVICE_TOKEN;
}

}  // namespace secret_logic
