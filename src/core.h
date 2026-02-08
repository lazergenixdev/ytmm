#ifndef HEADER_CORE_H
#define HEADER_CORE_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#define CJSON_HIDE_SYMBOLS
#include "vendor/cJSON.h"

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

#endif // HEADER_CORE_H
