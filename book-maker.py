#!/usr/bin/python3

import re, sys, shlex, time, subprocess, threading
import chess
import chess.polyglot


def move_to_polyglot_int(board, move):
    to_square = move.to_square
    from_square = move.from_square
    # Polyglot encodes castling moves with the from_square being where the king started
    # and the to_square being where the rook started, instead of the UCI standard of the
    # to_square being where the king ends up. Patch up this encoding.
    promotion = {
        None: 0,
        chess.KNIGHT: 1,
        chess.BISHOP: 2,
        chess.ROOK: 3,
        chess.QUEEN: 4,
    }[move.promotion]
    return to_square | (from_square << 6) | (promotion << 12)


def make_entry(board, move, weight=1, learn=0):
    key = chess.polyglot.zobrist_hash(board)
    raw_move = move_to_polyglot_int(board, move)
    return chess.polyglot.Entry(key=key, raw_move=raw_move, weight=weight, learn=learn)


def write_polyglot_bin(f, entries):
    entries = sorted(entries, key=lambda entry: entry.key)
    for entry in entries:
        f.write(chess.polyglot.ENTRY_STRUCT.pack(*entry))


class LeelaInterface:
    def __init__(self, command):
        self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stdin=subprocess.PIPE)

    def send(self, s):
        self.proc.stdin.write(bytes(s, encoding="ascii"))
        self.proc.stdin.flush()

    def readline(self):
        return self.proc.stdout.readline()

    def launch(self):
        self.send("position startpos\n")
        self.send("go infinite\n")
        self.thread = threading.Thread(target=self.clear_buffer)
        self.thread.setDaemon(True)
        self.thread.start()

    def clear_buffer(self):
        while True:
            line = self.readline()
            if not line:
                raise ValueError("Did lc0 die? Got back null line.")
            if b"bestmove" in line:
                print(" (stopping)")
                break
            print("\r> " + str(line.split(b" pv")[0].rstrip()), end=" ")
            sys.stdout.flush()

    def stop(self):
        self.send("stop\n")
        self.thread.join()

    def probe(self, moves):
        self.send("dumpnode moves%s\n" % "".join(" %s" % move.uci() for move in moves))
        children = {}
        while True:
            line = self.readline()
            if b"end-dump" in line:
                break
            try:
                move = chess.Move.from_uci(re.search("move=([^ ]+)", str(line)).groups()[0])
                visits = int(re.search("n=([^ ]+)", str(line)).groups()[0])
            except Exception as e:
                print("Line: %r" % (line,))
                raise e
            children[move] = visits
        return children


def explore_tree(args, leela, entries, board, moves, visit_threshold):
    indent = "  " * len(moves)
    children = leela.probe(moves)
    if not children:
        return
    best_move = max(children, key=children.__getitem__)
    if args.print_tree:
        print(indent + "%s -> %s" % (board.fen(), best_move))
    entries.append(make_entry(board, best_move, weight=min(0xffff, children[best_move] // 256)))
    for (move, visits) in children.items():
        if visits < visit_threshold:
            continue
        sub_board = board.copy()
        assert (sub_board.is_legal(move))
        sub_board.push(move)
        explore_tree(args, leela, entries, sub_board, moves + [move], visit_threshold)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", metavar="CMD", required=True,
                        help="Command to run lc0. (Split via shlex.split, and executed.)")
    parser.add_argument("--output", metavar="PATH", required=True, help="Output path to dump a .bin to.")
    parser.add_argument("--visit-threshold", metavar="INT", type=int, required=True,
                        help="Only write moves into the book if their parent has at least this many visits.")
    parser.add_argument("--dump-interval", metavar="SECONDS", type=int, default=60,
                        help="Dump a .bin every this many seconds.")
    parser.add_argument("--print-tree", action="store_true",
                        help="Print the book tree upon each probing. (May print lots of text.)")
    parser.add_argument("--multiwrite", action="store_true",
                        help="write multiple book files with visit thresholds increasing from 256 by factors of two")
    args = parser.parse_args()
    print("Options:", args)

    command = shlex.split(args.command)
    print("Command:", command)

    leela = LeelaInterface(command)

    while True:
        print("Running computation.")
        leela.launch()
        time.sleep(args.dump_interval)
        leela.stop()
        if (args.multiwrite):
            donewriting = False
            threshold = 256
            while not donewriting:
                print("Probing tree.")
                entries = []
                explore_tree(args, leela, entries, chess.Board(), [], threshold)
                if (len(entries) > 2):

                    print("Writing book with threshold ", threshold, end=' ')
                    sys.stdout.flush()
                    with open(args.output + "-" + str(threshold) + "n.bin", "wb") as f:
                        write_polyglot_bin(f, entries)
                    print("wrote %i entries." % len(entries))
                    threshold *= 2
                else:
                    donewriting = True
        else:
            print("Probing tree.")
            entries = []
            explore_tree(args, leela, entries, chess.Board(), [], args.visit_threshold)

            print("Writing book...", end=' ')
            sys.stdout.flush()
            with open(args.output, "wb") as f:
                write_polyglot_bin(f, entries)
            print("wrote %i entries." % len(entries))
