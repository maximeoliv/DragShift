// DragShift.exe — point d'entrée du paquet Windows (obligatoire pour un paquet « sparse »).
// Lance l'app Python : « pythonw dragshift.pyw », avec les chemins lus dans menu.tsv.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <string>

static std::wstring Widen(const std::string& s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    wchar_t buf[MAX_PATH];
    std::wstring dir(buf, GetModuleFileNameW(nullptr, buf, MAX_PATH));
    dir = dir.substr(0, dir.find_last_of(L"\\/"));

    HANDLE h = CreateFileW((dir + L"\\menu.tsv").c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0,
                           nullptr);
    if (h == INVALID_HANDLE_VALUE) return 1;
    std::string data(1 << 16, '\0');
    DWORD got = 0;
    ReadFile(h, &data[0], (DWORD)data.size(), &got, nullptr);
    CloseHandle(h);
    data.resize(got);

    std::wstring python, app;
    std::wstring text = Widen(data);
    size_t start = 0;
    while (start < text.size()) {
        size_t end = text.find(L'\n', start);
        std::wstring line = text.substr(start, end == std::wstring::npos ? std::wstring::npos : end - start);
        if (!line.empty() && line.back() == L'\r') line.pop_back();
        if (line.rfind(L"#python\t", 0) == 0) python = line.substr(8);
        if (line.rfind(L"#app\t", 0) == 0) app = line.substr(5);
        if (end == std::wstring::npos) break;
        start = end + 1;
    }
    if (python.empty() || app.empty()) return 1;

    std::wstring cmd = L"\"" + python + L"\" \"" + app + L"\"";
    STARTUPINFOW si{sizeof(si)};
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(python.c_str(), &cmd[0], nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi)) return 1;
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
