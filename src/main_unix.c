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

void do_thing()
{
    FILE *f = fopen("/Users/mbz/Music/dbbe46523f808e8cda7488da5ca4c5b2/music.json", "rb");
    if (!f) exit(1);
    fseek(f, 0, SEEK_END);
    off_t size = ftello(f);
    fseek(f, 0, SEEK_SET);
    void *data = malloc(size);
    fread(data, 1, size, f);
    fclose(f);

    {
        cJSON *json = cJSON_ParseWithLength(data, size);
        cJSON *data = cJSON_GetObjectItem(json, "data");
        if (!cJSON_IsArray(data))
            exit(2);
        
        cJSON *song;
        cJSON_ArrayForEach(song, data)
        {
            cJSON *id = cJSON_GetObjectItem(song, "id");
            if (!cJSON_IsString(id))
                exit(3);

            cJSON *year = cJSON_GetObjectItem(song, "year");
            if (!cJSON_IsNumber(year))
                year = NULL;

            cJSON *title = cJSON_GetObjectItem(song, "title");
            if (!cJSON_IsString(title))
                exit(5);

            if (year == NULL)
                printf("\x1b[90m%s\x1b[0m   --   \x1b[92m%s\x1b[0m\n", id->valuestring, title->valuestring);
            else
                printf("\x1b[90m%s\x1b[0m  %04i  \x1b[92m%s\x1b[0m\n", id->valuestring, year->valueint, title->valuestring);
        }

        cJSON_Delete(json);
    }
}

#define C(R,G,B)  "\x1b[38;2;" #R ";" #G ";" #B "m"
#define CB(R,G,B) "\x1b[1;38;2;" #R ";" #G ";" #B "m"
#define RESET     "\x1b[0m"

int main(int argc, char *argv[])
{
    printf(
CB(120,120,255) "usage:"RESET" " CB(255,140,255) "%s"RESET" [" C(100,200,100) "-h"RESET"] " C(100,200,100) "COMMAND ..." RESET "\n"
"\n"
"  " CB(255,255,255) "Youtube Music Manager (v2.0.0)\n"RESET
"\n"
CB(120,255,120) "COMMAND" RESET "\n"
"  " CB(120,255,120) "sync     "RESET" (" CB(0,200,0) "s"RESET") sync from music database\n"
"  " CB(120,255,120) "query    "RESET" (" CB(0,200,0) "q"RESET") query music from database\n"
"  " CB(120,255,120) "add      "RESET" (" CB(0,200,0) "a"RESET") add Youtube URL to database\n"
"  " CB(120,255,120) "remove   "RESET" (" CB(0,200,0) "r"RESET") remove Youtube URL from database\n"
"  " CB(120,255,120) "playlist "RESET" (" CB(0,200,0) "p"RESET") manage playlists\n"
"\n"
CB(120,120,255) "options:\n"RESET
"  " CB(120,255,120) "-h"RESET", "CB(120,240,255)"--help"RESET"    show this help message and exit\n",
    argv[0]);
    do_thing();
}

#include "core.c"
#include "vendor/cJSON.c"
