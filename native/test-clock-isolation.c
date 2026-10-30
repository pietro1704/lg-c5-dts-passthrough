/* SPDX-License-Identifier: LGPL-2.1-or-later
 * TARGET TEST ONLY. LD_PRELOAD for test-clock-target.py --isolated.
 * Redirect only the bridge's lease device and control socket. All GStreamer,
 * parsing, scheduling and production plugin bytes remain unchanged. No audio
 * device is opened; the fake lease uses /dev/null. Never install this library.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

static int lease = -1, active;
static unsigned long epoch;
static const char *endpoint(void) {
  const char *path = getenv("C5_CLOCK_SOCKET");
  return path && !strncmp(path, "/tmp/c5-clock-", 14) ? path : NULL;
}
int c5_clock_isolated(void) { return endpoint() != NULL; }

int open(const char *path, int flags, ...) {
  int (*real_open)(const char *, int, ...) = dlsym(RTLD_NEXT, "open");
  mode_t mode = 0;
  if (flags & O_CREAT) {
    va_list args; va_start(args, flags); mode = va_arg(args, int); va_end(args);
  }
  if (endpoint() && !strcmp(path, "/dev/c5_dts_session")) {
    if (lease >= 0) { errno = EBUSY; return -1; }
    lease = real_open("/dev/null", O_RDONLY | O_CLOEXEC);
    active = 0; epoch = 0;
    return lease;
  }
  return real_open(path, flags, mode);
}
int fstat(int fd, struct stat *info) {
  int (*real_fstat)(int, struct stat *) = dlsym(RTLD_NEXT, "fstat");
  int result = real_fstat(fd, info);
  if (!result && fd == lease) { info->st_mode = S_IFCHR | 0400; info->st_uid = 0; }
  return result;
}
int ioctl(int fd, unsigned long request, ...) {
  int (*real_ioctl)(int, unsigned long, ...) = dlsym(RTLD_NEXT, "ioctl");
  va_list args; va_start(args, request);
  unsigned long argument = va_arg(args, unsigned long); va_end(args);
  if (fd != lease || fd < 0) return real_ioctl(fd, request, argument);
  switch (request) {
    case _IO('D', 0x51): return 0x435502;
    case _IO('D', 0x52): case _IO('D', 0x55): active = 0; return 0;
    case _IO('D', 0x54):
      if (argument && argument > epoch) { epoch = argument; active = 1; return 0; }
      break;
    case _IO('D', 0x53): if (active && argument == epoch) return 0; break;
    default: errno = ENOTTY; return -1;
  }
  errno = ESTALE; return -1;
}
int close(int fd) {
  int (*real_close)(int) = dlsym(RTLD_NEXT, "close");
  if (fd == lease) { lease = -1; active = 0; epoch = 0; }
  return real_close(fd);
}
int connect(int fd, const struct sockaddr *address, socklen_t length) {
  int (*real_connect)(int, const struct sockaddr *, socklen_t) = dlsym(RTLD_NEXT, "connect");
  const char *path = endpoint();
  if (path && address->sa_family == AF_UNIX && length == sizeof(struct sockaddr_un)) {
    const struct sockaddr_un *unix_address = (const struct sockaddr_un *)address;
    if (!strncmp(unix_address->sun_path, "/run/c5-dts/control.sock", sizeof(unix_address->sun_path))) {
      struct sockaddr_un isolated = { .sun_family = AF_UNIX };
      if (strlen(path) >= sizeof(isolated.sun_path)) { errno = ENAMETOOLONG; return -1; }
      strcpy(isolated.sun_path, path);
      return real_connect(fd, (const struct sockaddr *)&isolated, sizeof(isolated));
    }
  }
  return real_connect(fd, address, length);
}
