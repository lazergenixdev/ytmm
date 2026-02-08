#include "core.h"
#include <sys/ioctl.h>
#include <unistd.h>

int term_get_width()
{
    struct winsize w;
    if (ioctl(STDOUT_FILENO, TIOCGWINSZ, &w) == -1)
    {
        perror("ioctl");
        return 1;
    }
    return w.ws_col;
}

int main(int argc, char *argv[])
{
	print_help();
}

#include "core.c"
#include "vendor/cJSON.c"
