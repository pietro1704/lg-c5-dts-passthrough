/* SPDX-License-Identifier: LGPL-2.1-or-later */
#ifndef DTS_NATIVE_SESSION_H
#define DTS_NATIVE_SESSION_H
#include <sys/socket.h>
#include <sys/un.h>
#include <poll.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#define C5_LEASE_API _IO('D', 0x51)
#define C5_LEASE_REVOKE _IO('D', 0x52)
#define C5_LEASE_CHECK _IO('D', 0x53)
#define C5_LEASE_RESUME _IO('D',0x54)
#define C5_LEASE_SUSPEND _IO('D',0x55)
#define C5_LEASE_VERSION 0x435502

static int kernel_lease_open(void) {
  struct stat status;
  int fd = open("/dev/c5_dts_session", O_RDONLY | O_CLOEXEC);
  if (fd < 0) return -1;
  if (fstat(fd, &status) || !S_ISCHR(status.st_mode) || status.st_uid != 0 ||
      (status.st_mode & 022) || ioctl(fd, C5_LEASE_API, 0) != C5_LEASE_VERSION) {
    close(fd); return -1;
  }
  return fd;
}

#ifndef DTS_SESSION_SOCKET
#define DTS_SESSION_SOCKET "/run/c5-dts/control.sock"
#endif

/* No media, URLs or application credentials cross this socket. The supervisor
 * derives the process identity from SO_PEERCRED, never from a claimed PID. */
static gboolean session_wait(int fd, short events, gint *cancel, gint64 deadline) {
  while (!g_atomic_int_get(cancel) && g_get_monotonic_time() < deadline) {
    struct pollfd p = {fd, events, 0};
    int n = poll(&p, 1, 20);
    if (n < 0 && errno == EINTR) continue;
    if (n < 0 || (p.revents & (POLLERR | POLLHUP | POLLNVAL))) return FALSE;
    if (n && (p.revents & events)) return TRUE;
  }
  return FALSE;
}

static gboolean session_exchange(int fd, const char *request, const char *reply,
                                gint *cancel, gboolean *busy) {
  gsize sent = 0, got = 0, n = strlen(request), expected = strlen(reply);
  gint64 deadline = g_get_monotonic_time() + 10 * G_USEC_PER_SEC;
  while (sent < n) {
    ssize_t result;
    if (!session_wait(fd, POLLOUT, cancel, deadline)) return FALSE;
    result = send(fd, request + sent, n - sent, MSG_NOSIGNAL | MSG_DONTWAIT);
    if (result < 0 && (errno == EINTR || errno == EAGAIN)) continue;
    if (result <= 0) return FALSE;
    sent += result;
  }
  while (got < expected) {
    char byte;
    ssize_t result;
    if (!session_wait(fd, POLLIN, cancel, deadline)) return FALSE;
    result = recv(fd, &byte, 1, MSG_DONTWAIT);
    if (result < 0 && (errno == EINTR || errno == EAGAIN)) continue;
    if (result != 1) return FALSE;
    if (byte != reply[got]) {
      if(got==0 && byte=='B')*busy=TRUE;
      return FALSE;
    }
    ++got;
  }
  return !g_atomic_int_get(cancel);
}

static int session_open_once(gint *cancel, gboolean *busy) {
  struct sockaddr_un address = {0};
  struct { int pid; unsigned int uid, gid; } peer;
  socklen_t size = sizeof(peer);
  int error = 0;
  socklen_t error_size = sizeof(error);
  int result;
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
  if (fd < 0) return -1;
  address.sun_family = AF_UNIX;
  g_strlcpy(address.sun_path, DTS_SESSION_SOCKET, sizeof(address.sun_path));
  /* Local connect has no network dependency; the bounded exchange handles a
   * supervisor which accepts but never arms. Only trust a root supervisor. */
  result = connect(fd, (struct sockaddr *)&address, sizeof(address));
  if ((result && (errno != EINPROGRESS ||
       !session_wait(fd, POLLOUT, cancel, g_get_monotonic_time()+2*G_USEC_PER_SEC))) ||
      getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &error_size) || error ||
      getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &size) ||
      size != sizeof(peer) || peer.uid != 0 ||
      !session_exchange(fd, "C5HD2\n", "ARMED\n", cancel, busy)) {
    close(fd);
    return -1;
  }
  return fd;
}
static int session_open(gint *cancel) {
  gint64 retry_until=g_get_monotonic_time()+2*G_USEC_PER_SEC;
  for(;;) {
    gboolean busy=FALSE;
    gint fd=session_open_once(cancel,&busy);
    if(fd>=0 || !busy || g_atomic_int_get(cancel) || g_get_monotonic_time()>=retry_until)return fd;
    g_usleep(20000);
  }
}
#endif
