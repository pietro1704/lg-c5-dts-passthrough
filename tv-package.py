# SPDX-License-Identifier: GPL-2.0-only
"""Offline-checkable TV entrypoints. Never reboot or change live audio routes."""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def load_backend(root):
    path = root / 'bundle/global_install.py'
    if not path.is_file() or path.is_symlink() or (root / 'bundle').is_symlink():
        raise RuntimeError('Pacote incompleto. Baixe o arquivo -tv.tar.gz da release; o arquivo de fontes não instala na TV.')
    spec = importlib.util.spec_from_file_location('tv_global_install', path)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    return backend


def present(path):
    return path.exists() or path.is_symlink()


def installed_paths(prefix, hook):
    return [prefix / 'current', prefix / 'installed.json', hook, prefix / 'cold-registry']


@contextmanager
def management_lock(backend, prefix):
    backend.secure_directory(prefix)
    path = prefix / 'management.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError('Arquivo de lock não pertence a esta instalação.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def remove_owned(backend, prefix, hook):
    # Validate ownership before even cleaning an empty interrupted preparation.
    backend.current_bundle(prefix, hook)
    registry = prefix / 'cold-registry'
    if present(registry):
        backend.secure_directory(registry)
        if not present(registry / 'state.json'):
            # Failed probing can leave an empty directory; anything else is
            # evidence that must not be guessed at or deleted recursively.
            if list(registry.iterdir()):
                raise RuntimeError('Backup sem journal: preservado para inspeção; remoção interrompida.')
            registry.rmdir()
    return backend.disable(prefix, hook)


def operate(action, check, backend, bundle, prefix, hook):
    if action == 'install':
        backend.verify(bundle)
        if check:
            return 'Pacote íntegro. Nada foi instalado. Confira modelo, firmware e pré-requisitos antes de instalar.'
    backend.require_target()
    if action == 'remove' and not any(present(p) for p in installed_paths(prefix, hook)):
        return 'Esta integração já está desativada. dts_restore e versões guardadas preservados.'
    if action == 'remove' and check:
        backend.current_bundle(prefix, hook)
        return 'Instalação identificada. Nada foi removido; a restauração ainda verificará o backup do registro.'
    if action == 'install' and present(hook.with_name('zz-c5-dts-passthrough')):
        raise RuntimeError('Instalação nativa antiga detectada. Não foi alterada; consulte a migração na wiki.')
    if not hook.parent.is_dir():
        raise RuntimeError('Diretório de boot do Homebrew não encontrado. Instale os pré-requisitos primeiro.')
    with management_lock(backend, prefix):
        if action == 'remove':
            # Recheck after taking the lock: another removal may have completed.
            if not any(present(p) for p in installed_paths(prefix, hook)):
                return 'Esta integração já está desativada.'
            remove_owned(backend, prefix, hook)
            return 'Integração desativada. Reinicie a TV manualmente para voltar ao estado anterior. dts_restore e versões guardadas preservados.'
        fresh = False
        try:
            result = backend.install(bundle, prefix, hook)
            fresh = result['boot'] == 'installed'
            current, _ = backend.current_bundle(prefix, hook)
            backend.cold_registry(current, prefix)
        except BaseException as error:
            if fresh:
                try:
                    remove_owned(backend, prefix, hook)
                except BaseException as rollback_error:
                    raise RuntimeError('Instalação incompleta; recuperação não concluída. Não reinicie ainda. '
                                       'Preserve os backups e execute sh remove.sh. Detalhe: ' + str(rollback_error)) from error
                raise RuntimeError('Instalação falhou; o boot novo foi desfeito; versões guardadas foram preservadas. '
                                   'Áudio atual não foi alterado. Detalhe: ' + str(error)) from error
            raise
        return 'Instalação concluída. Reinicie a TV manualmente para ativar. Para desfazer: sh remove.sh. Áudio atual preservado.'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'remove'])
    parser.add_argument('--check', action='store_true', help='somente verificar; não instalar/remover')
    args = parser.parse_args(argv)
    try:
        backend = load_backend(ROOT)
        print(operate(args.action, args.check, backend, ROOT / 'bundle', backend.PREFIX, backend.HOOK))
    except (OSError, ValueError, RuntimeError) as error:
        print('Erro: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrompido. Não reinicie antes de verificar o estado; use sh remove.sh para recuperação.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
