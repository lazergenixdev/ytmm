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
int mainpython(int argc, char *argv[]) {
    PyObject *pName, *pModule, *pClass;
    PyObject *pDict, *pInstance;
    PyObject *pOpts, *pUrlList;

    const char *url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

    // 1. Initialize Python
    Py_Initialize();

    // 2. Import yt_dlp
    pName = PyUnicode_FromString("yt_dlp");
    pModule = PyImport_Import(pName);
    Py_DECREF(pName);

    if (!pModule) {
        PyErr_Print();
        goto cleanup;
    }

    // 3. Get YoutubeDL class
    pClass = PyObject_GetAttrString(pModule, "YoutubeDL");
    if (!pClass || !PyCallable_Check(pClass)) {
        PyErr_Print();
        goto cleanup;
    }

    // 4. Build options dict
    pOpts = PyDict_New();
    PyDict_SetItemString(pOpts, "format", PyUnicode_FromString("ba"));
    PyDict_SetItemString(pOpts, "outtmpl", PyUnicode_FromString("%(id)s.mp3"));

    // 5. Create YoutubeDL instance
    pInstance = PyObject_CallFunctionObjArgs(pClass, pOpts, NULL);
    Py_DECREF(pOpts);

    if (!pInstance) {
        PyErr_Print();
        goto cleanup;
    }

    // 6. Create URL list
    pUrlList = PyList_New(1);
    PyList_SetItem(pUrlList, 0, PyUnicode_FromString(url)); // steals ref

    // 7. Call download()
    PyObject *result = PyObject_CallMethod(
        pInstance,
        "download",
        "O",
        pUrlList
    );

    Py_DECREF(pUrlList);

    if (!result) {
        PyErr_Print();
    } else {
        Py_DECREF(result);
    }

cleanup:
    Py_XDECREF(pInstance);
    Py_XDECREF(pClass);
    Py_XDECREF(pModule);

    // 8. Finalize Python
    Py_Finalize();
    return 0;
}

int main(int argc, char *argv[])
{
    printf("width: %d\n", term_get_width());
	print_help("ytmm.exe");
	do_thing("music.json");
	
	mainpython(argc, argv);
}

#include "core.c"
#include "vendor/cJSON.c"
