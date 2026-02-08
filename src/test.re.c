// re2c $INPUT -o $OUTPUT -i --case-ranges
#include <stdio.h>

int re2c_find_spaces() {

}

int re2c_find_extras(const char* YYCURSOR) {
    const char* YYMARKER;
    /*!re2c
        re2c:yyfill:enable = 0;
        re2c:define:YYCTYPE = "char";

        any = [^\x00] ;
        garbage = "Official" | "From" | "feat." ;

        "(" garbage any* ")" { return 1; }
        *                    { return 0; }
    */
}

int main(int argc, char *argv[]) {
    for (int i = 1; i < argc; ++i)
    {
        printf("%s => %i\n", argv[i], lex(argv[i]));
    }
    return 0;
}