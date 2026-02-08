#define NOB_IMPLEMENTATION
#define NOB_STRIP_PREFIX
#include "vendor/nob.h"

bool cmd_add_python(Cmd *cmd)
{
#if defined(_WIN32)
	const char* local_appdata = getenv("LOCALAPPDATA");
	assert(local_appdata && "Failed to get LOCALAPPDATA environment variable!");
	const char* python_path = temp_sprintf("%s\\Python\\pythoncore-3.14-64", local_appdata);
	assert(file_exists(python_path) && "Failed to locate python installation!");
	cmd_append(cmd, temp_sprintf("-I%s\\include", python_path));
	cmd_append(cmd, temp_sprintf("-L%s\\libs", python_path));
#elif defined(__APPLE__)
#	define PYTHON_FLAGS_PATH "macos-python-flags.temp"
	Cmd pycmd = {0};
	cmd_append(&pycmd, "python3.14-config", "--includes", "--ldflags", "--embed");
	assert(cmd_run(&pycmd, .stdout_path = PYTHON_FLAGS_PATH));
	String_Builder builder = {0};
	assert(read_entire_file(PYTHON_FLAGS_PATH, &builder));
	String_View sv = sb_to_sv(builder);
	while (sv.count != 0) {
		String_View arg = sv_chop_by_multi_delim(&sv, " \n");
		cmd_append(cmd, temp_sv_to_cstr(arg));
	}
#else
#	error "Need python path for this platform
#endif
	return true;
}

int main(int argc, char *argv[])
{
    GO_REBUILD_URSELF(argc, argv);

    Cmd cmd = {0};
    cmd_append(&cmd, "clang", "-Wall", "-Wextra");
    cmd_append(&cmd, "-g");
    cmd_append(&cmd, "-I.");
	cmd_add_python(&cmd);
#ifdef _WIN32
	cmd_append(&cmd, "-Wno-deprecated-declarations");
	cmd_append(&cmd, "src/main_win32.c");
    cmd_append(&cmd, "-oytmm.exe");
#else
	cmd_append(&cmd, "src/main_unix.c");
    cmd_append(&cmd, "-oytmm");
#endif
    assert(cmd_run(&cmd));
}
