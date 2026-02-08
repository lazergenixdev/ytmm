#define NOB_IMPLEMENTATION
#define NOB_STRIP_PREFIX
#include "vendor/nob.h"

int main(int argc, char *argv[])
{
    GO_REBUILD_URSELF(argc, argv);

    Cmd cmd = {0};
    cmd_append(&cmd, "clang", "-Wall", "-Wextra");
    cmd_append(&cmd, "-g");
    cmd_append(&cmd, "-I.");
    cmd_append(&cmd, "-oytmm");
    cmd_append(&cmd, "src/main_unix.c");
    assert(cmd_run(&cmd));
}
