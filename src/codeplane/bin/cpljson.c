/*
 * cpljson — CodePlane sidecar cache CLI
 *
 * A minimal cross-platform C binary that queries the running CodePlane
 * daemon's sidecar cache endpoints.  Injected into .codeplane/bin/ at
 * `cpl init` / `cpl up --reindex` time.
 *
 * Usage:
 *   cpljson list   --session S --endpoint E
 *   cpljson slice  --cache C  [--path P] [--max-bytes N] [--offset N]
 *   cpljson meta   --cache C
 *
 * Configuration is read from:
 *   .codeplane/run/server.json  →  {"port": 7777}
 *   .codeplane/run/token        →  bearer token (optional)
 *
 * Build:
 *   Linux/macOS:  cc -O2 -o cpljson cpljson.c
 *   Windows:      cl /O2 cpljson.c /Fe:cpljson.exe ws2_32.lib
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---------- platform abstraction ---------- */
#ifdef _WIN32
  #include <winsock2.h>
  #include <ws2tcpip.h>
  #pragma comment(lib, "ws2_32.lib")
  typedef SOCKET sock_t;
  #define SOCK_INVALID INVALID_SOCKET
  #define CLOSE_SOCK closesocket
  static int  net_init(void) {
      WSADATA d; return WSAStartup(MAKEWORD(2,2), &d);
  }
  static void net_cleanup(void) { WSACleanup(); }
#else
  #include <unistd.h>
  #include <sys/socket.h>
  #include <netinet/in.h>
  #include <arpa/inet.h>
  #include <netdb.h>
  typedef int sock_t;
  #define SOCK_INVALID (-1)
  #define CLOSE_SOCK close
  static int  net_init(void)    { return 0; }
  static void net_cleanup(void) {}
#endif

/* ---------- constants ---------- */
#define BUF_SIZE      (256 * 1024)
#define MAX_PATH_LEN  4096
#define MAX_URL_LEN   8192

/* ---------- helpers ---------- */

/* Find .codeplane/ by walking up from cwd */
static int find_codeplane_dir(char *out, size_t out_sz) {
    char cwd[MAX_PATH_LEN];
#ifdef _WIN32
    if (!_getcwd(cwd, sizeof(cwd))) return -1;
#else
    if (!getcwd(cwd, sizeof(cwd))) return -1;
#endif
    char probe[MAX_PATH_LEN];
    for (;;) {
        snprintf(probe, sizeof(probe), "%s/.codeplane", cwd);
        FILE *f = fopen(probe, "r");
        if (f) { fclose(f); break; }
        /* Try as directory */
        snprintf(probe, sizeof(probe), "%s/.codeplane/config.yaml", cwd);
        f = fopen(probe, "r");
        if (f) {
            fclose(f);
            snprintf(out, out_sz, "%s/.codeplane", cwd);
            return 0;
        }
        /* Go up one level */
        char *sep = strrchr(cwd, '/');
#ifdef _WIN32
        if (!sep) sep = strrchr(cwd, '\\');
#endif
        if (!sep || sep == cwd) return -1;
        *sep = '\0';
    }
    snprintf(out, out_sz, "%s/.codeplane", cwd);
    return 0;
}

/* Read port from .codeplane/run/server.json (minimal JSON parse) */
static int read_port(const char *cpl_dir) {
    char path[MAX_PATH_LEN];
    snprintf(path, sizeof(path), "%s/run/server.json", cpl_dir);
    FILE *f = fopen(path, "r");
    if (!f) {
        /* Fall back to config.yaml port line */
        snprintf(path, sizeof(path), "%s/config.yaml", cpl_dir);
        f = fopen(path, "r");
        if (!f) return 7777; /* default */
        char line[256];
        while (fgets(line, sizeof(line), f)) {
            int p;
            if (sscanf(line, "port: %d", &p) == 1) { fclose(f); return p; }
        }
        fclose(f);
        return 7777;
    }
    char buf[512];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = '\0';
    /* Find "port": <number> */
    const char *pp = strstr(buf, "\"port\"");
    if (!pp) pp = strstr(buf, "'port'");
    if (pp) {
        pp = strchr(pp, ':');
        if (pp) return atoi(pp + 1);
    }
    return 7777;
}

/* Read bearer token from .codeplane/run/token */
static int read_token(const char *cpl_dir, char *out, size_t out_sz) {
    char path[MAX_PATH_LEN];
    snprintf(path, sizeof(path), "%s/run/token", cpl_dir);
    FILE *f = fopen(path, "r");
    if (!f) { out[0] = '\0'; return 0; }
    size_t n = fread(out, 1, out_sz - 1, f);
    fclose(f);
    out[n] = '\0';
    /* Trim trailing whitespace */
    while (n > 0 && (out[n-1] == '\n' || out[n-1] == '\r' || out[n-1] == ' '))
        out[--n] = '\0';
    return (int)n;
}

/* Simple HTTP GET and print response body */
static int http_get(int port, const char *path_and_query, const char *token) {
    if (net_init() != 0) {
        fprintf(stderr, "cpljson: network init failed\n");
        return 1;
    }

    sock_t s = socket(AF_INET, SOCK_STREAM, 0);
    if (s == SOCK_INVALID) {
        fprintf(stderr, "cpljson: socket() failed\n");
        net_cleanup();
        return 1;
    }

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((unsigned short)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    if (connect(s, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        fprintf(stderr, "cpljson: cannot connect to localhost:%d\n", port);
        CLOSE_SOCK(s);
        net_cleanup();
        return 1;
    }

    /* Build HTTP/1.0 request */
    char req[MAX_URL_LEN + 512];
    int req_len;
    if (token[0]) {
        req_len = snprintf(req, sizeof(req),
            "GET %s HTTP/1.0\r\n"
            "Host: localhost:%d\r\n"
            "Authorization: Bearer %s\r\n"
            "Accept: application/json\r\n"
            "\r\n",
            path_and_query, port, token);
    } else {
        req_len = snprintf(req, sizeof(req),
            "GET %s HTTP/1.0\r\n"
            "Host: localhost:%d\r\n"
            "Accept: application/json\r\n"
            "\r\n",
            path_and_query, port);
    }

    send(s, req, req_len, 0);

    /* Read response */
    char *buf = malloc(BUF_SIZE);
    if (!buf) { CLOSE_SOCK(s); net_cleanup(); return 1; }
    size_t total = 0;
    for (;;) {
        int n = recv(s, buf + total, (int)(BUF_SIZE - 1 - total), 0);
        if (n <= 0) break;
        total += (size_t)n;
        if (total >= BUF_SIZE - 1) break;
    }
    buf[total] = '\0';
    CLOSE_SOCK(s);
    net_cleanup();

    /* Skip HTTP headers — find \r\n\r\n */
    const char *body = strstr(buf, "\r\n\r\n");
    if (body) {
        body += 4;
    } else {
        body = buf; /* fallback: print everything */
    }

    /* Check for HTTP error */
    int http_status = 0;
    if (strncmp(buf, "HTTP/", 5) == 0) {
        const char *sp = strchr(buf, ' ');
        if (sp) http_status = atoi(sp + 1);
    }

    printf("%s\n", body);
    free(buf);

    return (http_status >= 200 && http_status < 300) ? 0 : 1;
}

/* URL-encode a string (minimal: encode spaces, &, =, ?, etc.) */
static void url_encode(const char *src, char *dst, size_t dst_sz) {
    size_t i = 0;
    while (*src && i + 4 < dst_sz) {
        unsigned char c = (unsigned char)*src;
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
            dst[i++] = (char)c;
        } else {
            i += (size_t)snprintf(dst + i, dst_sz - i, "%%%02X", c);
        }
        src++;
    }
    dst[i] = '\0';
}

/* ---------- subcommands ---------- */

static void usage(void) {
    fprintf(stderr,
        "Usage:\n"
        "  cpljson list  --session S --endpoint E\n"
        "  cpljson slice --cache C [--path P] [--max-bytes N] [--offset N]\n"
        "  cpljson meta  --cache C\n"
    );
}

static const char *find_arg(int argc, char **argv, const char *name) {
    for (int i = 0; i < argc - 1; i++) {
        if (strcmp(argv[i], name) == 0) return argv[i + 1];
    }
    return NULL;
}

int main(int argc, char **argv) {
    if (argc < 2) { usage(); return 1; }

    const char *cmd = argv[1];

    char cpl_dir[MAX_PATH_LEN];
    if (find_codeplane_dir(cpl_dir, sizeof(cpl_dir)) != 0) {
        fprintf(stderr, "cpljson: .codeplane/ not found (are you in a CodePlane repo?)\n");
        return 1;
    }

    int port = read_port(cpl_dir);
    char token[512];
    read_token(cpl_dir, token, sizeof(token));

    char url[MAX_URL_LEN];
    char enc1[1024], enc2[1024];

    if (strcmp(cmd, "list") == 0) {
        const char *session  = find_arg(argc, argv, "--session");
        const char *endpoint = find_arg(argc, argv, "--endpoint");
        if (!session || !endpoint) {
            fprintf(stderr, "cpljson list: --session and --endpoint required\n");
            return 1;
        }
        url_encode(session, enc1, sizeof(enc1));
        url_encode(endpoint, enc2, sizeof(enc2));
        snprintf(url, sizeof(url), "/sidecar/cache/list?session=%s&endpoint=%s", enc1, enc2);
        return http_get(port, url, token);

    } else if (strcmp(cmd, "slice") == 0) {
        const char *cache_id  = find_arg(argc, argv, "--cache");
        const char *path      = find_arg(argc, argv, "--path");
        const char *max_bytes = find_arg(argc, argv, "--max-bytes");
        const char *offset    = find_arg(argc, argv, "--offset");
        if (!cache_id) {
            fprintf(stderr, "cpljson slice: --cache required\n");
            return 1;
        }
        url_encode(cache_id, enc1, sizeof(enc1));
        int pos = snprintf(url, sizeof(url), "/sidecar/cache/slice?cache=%s", enc1);
        if (path) {
            url_encode(path, enc2, sizeof(enc2));
            pos += snprintf(url + pos, sizeof(url) - (size_t)pos, "&path=%s", enc2);
        }
        if (max_bytes) {
            pos += snprintf(url + pos, sizeof(url) - (size_t)pos, "&max_bytes=%s", max_bytes);
        }
        if (offset) {
            snprintf(url + pos, sizeof(url) - (size_t)pos, "&offset=%s", offset);
        }
        return http_get(port, url, token);

    } else if (strcmp(cmd, "meta") == 0) {
        const char *cache_id = find_arg(argc, argv, "--cache");
        if (!cache_id) {
            fprintf(stderr, "cpljson meta: --cache required\n");
            return 1;
        }
        url_encode(cache_id, enc1, sizeof(enc1));
        snprintf(url, sizeof(url), "/sidecar/cache/meta?cache=%s", enc1);
        return http_get(port, url, token);

    } else {
        fprintf(stderr, "cpljson: unknown command '%s'\n", cmd);
        usage();
        return 1;
    }
}
