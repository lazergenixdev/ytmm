#include "core.h"
#include <windows.h>

int term_get_width()
{
    CONSOLE_SCREEN_BUFFER_INFO csbi;
    int columns, rows;

    GetConsoleScreenBufferInfo(GetStdHandle(STD_OUTPUT_HANDLE), &csbi);
    columns = csbi.srWindow.Right - csbi.srWindow.Left + 1;
    rows = csbi.srWindow.Bottom - csbi.srWindow.Top + 1;

	return columns;
}

int main(int argc, char *argv[])
{
    printf("width: %d\n", term_get_width());
	print_help("ytmm.exe");
	do_thing("music.json");
	
	Py_Initialize();
	PyRun_SimpleString("import yt_dlp; print(yt_dlp)");
	Py_Finalize();
}

#include "core.c"
#include "vendor/cJSON.c"
