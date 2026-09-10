"""Opt-in argcomplete support with no monitoring side effects."""
import argparse
import os


def complete(parser):
    if '_ARGCOMPLETE' not in os.environ:
        return
    try:
        import argcomplete
    except ImportError:
        raise SystemExit('Install dmux-ml[shell] for shell completions') from None
    argcomplete.autocomplete(parser)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='dmux completions')
    parser.add_argument('shell', choices=('bash', 'zsh'))
    args = parser.parse_args(argv)
    try:
        import argcomplete
    except ImportError:
        parser.exit(2, 'Install dmux-ml[shell] for shell completions\n')
    print(argcomplete.shellcode(['dmux'], shell=args.shell))
