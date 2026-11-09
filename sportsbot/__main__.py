import sys


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        from .runner import run
        run()
    elif cmd == "selftest":
        from .selftest import main as st
        sys.exit(0 if st() else 1)
    else:
        print("usage: python -m sportsbot [run|selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
