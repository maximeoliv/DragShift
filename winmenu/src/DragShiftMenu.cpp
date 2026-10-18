// DragShiftMenu.dll — entrées « Convertir en › » et « Modifier l'image › » (ou le PDF, la vidéo…) du menu
// clic droit moderne de Windows 11 (IExplorerCommand).
//
// Contraintes du menu moderne, découvertes en test :
//   - un paquet qui déclare plusieurs entrées est regroupé sous le nom de l'app (« DragShift › ») :
//     chaque entrée racine a donc son propre paquet « sparse » (DragShift.Convert, DragShift.Tools) ;
//   - un seul niveau de sous-menu (un sous-menu imbriqué s'affiche vide) ;
//   - pas de séparateur (ECF_ISSEPARATOR s'affiche comme un élément « - ») : on les saute.
//
// La DLL ne contient aucune liste de formats : elle lit menu.tsv (écrit par shellmenu.py, à côté de la
// DLL), et au clic elle passe toute la sélection à send.pyw, qui la transmet à l'app DragShift.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shobjidl.h>
#include <shlwapi.h>
#include <new>
#include <string>
#include <vector>
#include <map>

// {22E1DDD1-42FE-4A5F-B9B7-0A94AA9566F5} : « Convertir en » (paquet DragShift.Convert)
static const CLSID CLSID_Convert = {0x22e1ddd1, 0x42fe, 0x4a5f, {0xb9, 0xb7, 0x0a, 0x94, 0xaa, 0x95, 0x66, 0xf5}};
// {8714DB24-1431-4FFF-82F5-E496EBF6393F} : « Modifier l'image / le PDF / … » (paquet DragShift.Tools)
static const CLSID CLSID_Tools = {0x8714db24, 0x1431, 0x4fff, {0x82, 0xf5, 0xe4, 0x96, 0xeb, 0xf6, 0x39, 0x3f}};

static HMODULE g_module = nullptr;
static LONG g_objects = 0;  // objets vivants (pour DllCanUnloadNow)

// ------------------------------------------------------------------ configuration (menu.tsv)

struct Entry {
    std::wstring action, label;
};
struct Menu {
    std::wstring title;
    std::vector<Entry> entries;
};
struct Config {
    std::wstring python, send, icon;
    std::map<std::wstring, Menu> convert, tools;  // extension -> menu
    FILETIME stamp{};
};

static Config g_cfg;
static SRWLOCK g_lock = SRWLOCK_INIT;
static std::wstring g_lastExt;  // extension de la dernière sélection vue (GetState/GetTitle)

static std::wstring ModuleDir() {
    wchar_t buf[MAX_PATH];
    DWORD n = GetModuleFileNameW(g_module, buf, MAX_PATH);
    std::wstring p(buf, n);
    size_t slash = p.find_last_of(L"\\/");
    return slash == std::wstring::npos ? p : p.substr(0, slash);
}

static std::wstring Widen(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

static std::vector<std::wstring> Split(const std::wstring& line, wchar_t sep) {
    std::vector<std::wstring> out;
    size_t start = 0;
    for (;;) {
        size_t pos = line.find(sep, start);
        out.push_back(line.substr(start, pos == std::wstring::npos ? std::wstring::npos : pos - start));
        if (pos == std::wstring::npos) break;
        start = pos + 1;
    }
    return out;
}

// Recharge menu.tsv s'il a changé (réinstallation de DragShift, nouvelle version…).
static void LoadConfig() {
    std::wstring path = ModuleDir() + L"\\menu.tsv";
    WIN32_FILE_ATTRIBUTE_DATA attr{};
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &attr)) return;
    AcquireSRWLockExclusive(&g_lock);
    if (CompareFileTime(&attr.ftLastWriteTime, &g_cfg.stamp) == 0) {
        ReleaseSRWLockExclusive(&g_lock);
        return;
    }
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h != INVALID_HANDLE_VALUE) {
        std::string data(attr.nFileSizeLow, '\0');
        DWORD got = 0;
        ReadFile(h, &data[0], attr.nFileSizeLow, &got, nullptr);
        CloseHandle(h);
        data.resize(got);
        Config cfg;
        cfg.stamp = attr.ftLastWriteTime;
        for (const std::wstring& raw : Split(Widen(data), L'\n')) {
            std::wstring line = raw;
            if (!line.empty() && line.back() == L'\r') line.pop_back();
            std::vector<std::wstring> f = Split(line, L'\t');
            if (f.size() == 2 && f[0] == L"#python") cfg.python = f[1];
            else if (f.size() == 2 && f[0] == L"#send") cfg.send = f[1];
            else if (f.size() == 2 && f[0] == L"#icon") cfg.icon = f[1];
            else if (f.size() == 5 && !f[0].empty() && f[0][0] != L'#') {
                Menu& m = (f[1] == L"tools" ? cfg.tools : cfg.convert)[f[0]];
                m.title = f[2];
                m.entries.push_back({f[3], f[4]});
            }
        }
        g_cfg = cfg;
    }
    ReleaseSRWLockExclusive(&g_lock);
}

// Extension (minuscules, sans le point) du premier élément sélectionné ; vide si ce n'est pas un fichier.
static std::wstring FirstExtension(IShellItemArray* items) {
    if (!items) return {};
    IShellItem* item = nullptr;
    if (FAILED(items->GetItemAt(0, &item))) return {};
    std::wstring ext;
    PWSTR path = nullptr;
    if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
        SFGAOF attrs = 0;
        if (SUCCEEDED(item->GetAttributes(SFGAO_FOLDER, &attrs)) && !(attrs & SFGAO_FOLDER)) {
            PCWSTR dot = PathFindExtensionW(path);
            if (dot && *dot == L'.') {
                ext = dot + 1;
                CharLowerBuffW(&ext[0], (DWORD)ext.size());
            }
        }
        CoTaskMemFree(path);
    }
    item->Release();
    return ext;
}

static bool FindMenu(bool tools, const std::wstring& ext, Menu& out) {
    LoadConfig();
    AcquireSRWLockShared(&g_lock);
    const auto& table = tools ? g_cfg.tools : g_cfg.convert;
    auto it = table.find(ext);
    bool ok = it != table.end() && !it->second.entries.empty();
    if (ok) out = it->second;
    ReleaseSRWLockShared(&g_lock);
    return ok;
}

// ------------------------------------------------------------------ lancement de l'action

// Écrit les chemins sélectionnés dans un fichier temporaire et lance « pythonw send.pyw <action> @<fichier> ».
// (Un fichier plutôt que la ligne de commande : pas de limite de longueur avec des centaines de fichiers.)
static HRESULT Launch(const std::wstring& action, IShellItemArray* items) {
    if (!items) return E_INVALIDARG;
    std::wstring python, send;
    AcquireSRWLockShared(&g_lock);
    python = g_cfg.python;
    send = g_cfg.send;
    ReleaseSRWLockShared(&g_lock);
    if (python.empty() || send.empty()) return E_FAIL;

    std::wstring list;
    DWORD count = 0;
    items->GetCount(&count);
    for (DWORD i = 0; i < count; ++i) {
        IShellItem* item = nullptr;
        if (FAILED(items->GetItemAt(i, &item))) continue;
        PWSTR path = nullptr;
        if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
            list += path;
            list += L"\n";
            CoTaskMemFree(path);
        }
        item->Release();
    }
    if (list.empty()) return S_OK;

    wchar_t dir[MAX_PATH], file[MAX_PATH];
    GetTempPathW(MAX_PATH, dir);
    if (!GetTempFileNameW(dir, L"dsh", 0, file)) return E_FAIL;
    int n = WideCharToMultiByte(CP_UTF8, 0, list.c_str(), (int)list.size(), nullptr, 0, nullptr, nullptr);
    std::string utf8(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, list.c_str(), (int)list.size(), &utf8[0], n, nullptr, nullptr);
    HANDLE h = CreateFileW(file, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, nullptr);
    if (h == INVALID_HANDLE_VALUE) return E_FAIL;
    DWORD written = 0;
    WriteFile(h, utf8.data(), (DWORD)utf8.size(), &written, nullptr);
    CloseHandle(h);

    std::wstring cmd = L"\"" + python + L"\" \"" + send + L"\" " + action + L" \"@" + file + L"\"";
    AllowSetForegroundWindow(ASFW_ANY);  // DragShift pourra afficher sa fenêtre au premier plan
    STARTUPINFOW si{sizeof(si)};
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(python.c_str(), &cmd[0], nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi)) {
        DeleteFileW(file);
        return HRESULT_FROM_WIN32(GetLastError());
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return S_OK;
}

// ------------------------------------------------------------------ objets COM

template <class Base>
class Unknown : public Base {
  public:
    Unknown() { InterlockedIncrement(&g_objects); }
    virtual ~Unknown() { InterlockedDecrement(&g_objects); }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        LONG r = InterlockedDecrement(&refs_);
        if (r == 0) delete this;
        return r;
    }
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == __uuidof(Base)) {
            *ppv = static_cast<Base*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }

  private:
    LONG refs_ = 1;
};

class CommandBase : public Unknown<IExplorerCommand> {
  public:
    IFACEMETHODIMP GetIcon(IShellItemArray*, LPWSTR* icon) override {
        *icon = nullptr;
        return E_NOTIMPL;
    }
    IFACEMETHODIMP GetToolTip(IShellItemArray*, LPWSTR* tip) override {
        *tip = nullptr;
        return E_NOTIMPL;
    }
    IFACEMETHODIMP GetCanonicalName(GUID* guid) override {
        *guid = GUID_NULL;
        return S_OK;
    }
    IFACEMETHODIMP Invoke(IShellItemArray*, IBindCtx*) override { return E_NOTIMPL; }
    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** e) override {
        *e = nullptr;
        return E_NOTIMPL;
    }
};

class SubCommand : public CommandBase {
  public:
    SubCommand(const Entry& e, const std::wstring& icon) : entry_(e), icon_(icon) {}
    IFACEMETHODIMP GetTitle(IShellItemArray*, LPWSTR* name) override { return SHStrDupW(entry_.label.c_str(), name); }
    IFACEMETHODIMP GetIcon(IShellItemArray*, LPWSTR* icon) override {
        *icon = nullptr;
        return icon_.empty() ? E_NOTIMPL : SHStrDupW(icon_.c_str(), icon);
    }
    IFACEMETHODIMP GetState(IShellItemArray*, BOOL, EXPCMDSTATE* state) override {
        *state = ECS_ENABLED;
        return S_OK;
    }
    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override {
        *flags = ECF_DEFAULT;
        return S_OK;
    }
    IFACEMETHODIMP Invoke(IShellItemArray* items, IBindCtx*) override { return Launch(entry_.action, items); }

  private:
    Entry entry_;
    std::wstring icon_;
};

class CommandEnum : public Unknown<IEnumExplorerCommand> {
  public:
    explicit CommandEnum(std::vector<IExplorerCommand*> cmds) : cmds_(std::move(cmds)) {}
    ~CommandEnum() override {
        for (auto* c : cmds_) c->Release();
    }
    IFACEMETHODIMP Next(ULONG want, IExplorerCommand** out, ULONG* fetched) override {
        ULONG got = 0;
        while (got < want && pos_ < cmds_.size()) {
            out[got] = cmds_[pos_++];
            out[got]->AddRef();
            ++got;
        }
        if (fetched) *fetched = got;
        return got == want ? S_OK : S_FALSE;
    }
    IFACEMETHODIMP Skip(ULONG n) override {
        pos_ = (pos_ + n > cmds_.size()) ? cmds_.size() : pos_ + n;
        return S_OK;
    }
    IFACEMETHODIMP Reset() override {
        pos_ = 0;
        return S_OK;
    }
    IFACEMETHODIMP Clone(IEnumExplorerCommand** e) override {
        *e = nullptr;
        return E_NOTIMPL;
    }

  private:
    std::vector<IExplorerCommand*> cmds_;
    size_t pos_ = 0;
};

class RootCommand : public CommandBase {
  public:
    explicit RootCommand(bool tools) : tools_(tools) {}

    IFACEMETHODIMP GetTitle(IShellItemArray* items, LPWSTR* name) override {
        Menu m;
        if (!FindMenu(tools_, Remember(items), m)) return E_FAIL;
        return SHStrDupW(m.title.c_str(), name);
    }
    IFACEMETHODIMP GetIcon(IShellItemArray*, LPWSTR* icon) override {
        LoadConfig();
        AcquireSRWLockShared(&g_lock);
        std::wstring path = g_cfg.icon;
        ReleaseSRWLockShared(&g_lock);
        *icon = nullptr;
        return path.empty() ? E_NOTIMPL : SHStrDupW(path.c_str(), icon);
    }
    IFACEMETHODIMP GetState(IShellItemArray* items, BOOL, EXPCMDSTATE* state) override {
        Menu m;
        *state = FindMenu(tools_, Remember(items), m) ? ECS_ENABLED : ECS_HIDDEN;
        return S_OK;
    }
    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override {
        *flags = ECF_HASSUBCOMMANDS;
        return S_OK;
    }
    // EnumSubCommands ne reçoit pas la sélection : on réutilise l'extension vue dans GetState/GetTitle.
    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** out) override {
        *out = nullptr;
        std::wstring ext = ext_;
        if (ext.empty()) {
            AcquireSRWLockShared(&g_lock);
            ext = g_lastExt;
            ReleaseSRWLockShared(&g_lock);
        }
        Menu m;
        if (!FindMenu(tools_, ext, m)) return E_FAIL;
        AcquireSRWLockShared(&g_lock);
        std::wstring icon = g_cfg.icon;
        ReleaseSRWLockShared(&g_lock);
        std::vector<IExplorerCommand*> cmds;
        for (const Entry& e : m.entries) {
            if (e.action == L"-") continue;  // pas de séparateur dans le menu moderne
            auto* c = new (std::nothrow) SubCommand(e, e.action == L"menu" ? icon : std::wstring());
            if (c) cmds.push_back(c);
        }
        auto* en = new (std::nothrow) CommandEnum(std::move(cmds));
        if (!en) return E_OUTOFMEMORY;
        *out = en;
        return S_OK;
    }

  private:
    const std::wstring& Remember(IShellItemArray* items) {
        std::wstring ext = FirstExtension(items);
        if (!ext.empty()) {
            ext_ = ext;
            AcquireSRWLockExclusive(&g_lock);
            g_lastExt = ext;
            ReleaseSRWLockExclusive(&g_lock);
        }
        return ext_;
    }
    bool tools_;
    std::wstring ext_;
};

class Factory : public Unknown<IClassFactory> {
  public:
    explicit Factory(bool tools) : tools_(tools) {}
    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** ppv) override {
        *ppv = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* cmd = new (std::nothrow) RootCommand(tools_);
        if (!cmd) return E_OUTOFMEMORY;
        HRESULT hr = cmd->QueryInterface(riid, ppv);
        cmd->Release();
        return hr;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) InterlockedIncrement(&g_objects);
        else InterlockedDecrement(&g_objects);
        return S_OK;
    }

  private:
    bool tools_;
};

// ------------------------------------------------------------------ exports

extern "C" BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = inst;
        DisableThreadLibraryCalls(inst);
    }
    return TRUE;
}

extern "C" HRESULT __stdcall DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv) {
    *ppv = nullptr;
    bool tools;
    if (clsid == CLSID_Convert) tools = false;
    else if (clsid == CLSID_Tools) tools = true;
    else return CLASS_E_CLASSNOTAVAILABLE;
    auto* f = new (std::nothrow) Factory(tools);
    if (!f) return E_OUTOFMEMORY;
    HRESULT hr = f->QueryInterface(riid, ppv);
    f->Release();
    return hr;
}

extern "C" HRESULT __stdcall DllCanUnloadNow() { return g_objects == 0 ? S_OK : S_FALSE; }
