#ifndef HEADER_CORE_H
#define HEADER_CORE_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#define CJSON_HIDE_SYMBOLS
#include "vendor/cJSON.h"
#include "Python.h"

typedef uint8_t  u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

typedef u32 color;
typedef const char *cstring;
typedef const char *cstring_list; // Double null terminated (Ex: "a\0b\0c\0\0")
typedef struct { u64 low; u32 high; } base64;
typedef struct { u64 count; char *data; } string;

typedef struct {
    u32 count;
    u32 capacity;
    void *data;
} Arena;

typedef struct {
    u64  id_lo;
    u32  id_hi_year; // high 16 bits == year, low 16 bits == id high
    u32  title;
    u32  album;
    u32  artists;
} Song;

typedef struct {
    Arena title_arena;
    Arena album_arena;
    Arena artists_arena;
    Arena songs_arena;
    u32   count;
} Music_Database;

base64 b64_from_str(string text);
string str_from_b64(Arena *arena, base64 number);
string str_reverse_inplace(string text);
string arena_push_str(Arena *arena, string text);
cstring arena_push_cstr(Arena *arena, cstring text);
void arena_push_byte(Arena *arena, u8 byte);
void* arena_push_raw(Arena *arena, void *data, u32 size);

int term_get_width();

static void do_thing(const char *music)
{
    FILE *f = fopen(music, "rb");
    if (!f) exit(1);
    fseek(f, 0, SEEK_END);
    size_t size = ftell(f);
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

void print_help(const char* name)
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
    name);
}

#endif // HEADER_CORE_H
