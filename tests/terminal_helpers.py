"""Compare terminal modes without conflating Darwin's pending-input state."""
import sys
import termios


def terminal_settings(attributes, *, platform=sys.platform, pendin=termios.PENDIN):
    settings = list(attributes)
    if platform == "darwin":
        # XNU sets PENDIN when restoring ICANON with TCSADRAIN, even if the
        # supplied settings do not contain it. It is cleared by input processing.
        # https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/tty.c#L1313-L1345
        settings[3] &= ~pendin
    return settings
