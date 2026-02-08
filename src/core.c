#include "core.h"

base64 b64_from_str(string text)
{
    base64 result = {0};
    for (u64 i = 0; i < text.count; ++i)
    {
        u32 character = text.data[i];
        u64 digit = 63;
        
        if ('A' <= character && character <= 'Z')
            digit = character - 'A';
        else if ('a' <= character && character <= 'z')
            digit = character - 'a' + 26;
        else if ('0' <= character && character <= '9')
            digit = character - '0' + 52;
        else if (character == '-')
            digit = 62;

        if ((result.low >> 54) != 0)
        {
            u64 remainder = result.low >> 54;
            result.high = (result.high * 64) | remainder;
            result.low ^= (remainder << 54);
        }
        
        result.low = (result.low * 64) | digit;
    }
    return result;
}

string str_from_b64(Arena *arena, base64 number)
{
    static char scratch_buffer[20];
    int offset = 0;

    while (number.low != 0 || number.high != 0)
    {
        if (number.low == 0)
        {
            number.low = (u64)(number.high);
            number.high = 0;
        }

        u64 digit = number.low & 0x3F;
        number.low /= 64;

        char character = '_';
        if (digit < 26)
            character = digit + 'A';
        else if (digit < 52)
            character = digit + 'a' - 26;
        else if (digit < 62)
            character = digit + '0' - 52;
        else if (digit == 62)
            character = '-';

        scratch_buffer[offset++] = character;
    }

    string scratch_text = {.count = offset, .data = scratch_buffer};
    return str_reverse_inplace(scratch_text);
    //return arena_insert_string(arena, string_reverse_inplace(scratch_text));
}

string str_reverse_inplace(string text)
{
    for (u64 i = 0; i < text.count / 2; ++i)
    {
        char t = text.data[i];
        text.data[i] = text.data[text.count - i - 1];
        text.data[text.count - i - 1] = t;
    }
    return text;
}

string arena_insert_str(Arena *arena, string text)
{
    return (string){};
}
