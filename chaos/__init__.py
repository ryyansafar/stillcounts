"""Deterministic simulation harness for the Still Counts sync protocol.

One thread and one seeded `random.Random` drive everything: virtual time, the network (drop, duplicate, reorder,
delay, partition), server crashes and torn log lines, phone reloads and reinstalls, Lamport jumps. The code under test
is the real `server.app` (driven in-process over ASGI), the real `stillcounts.crdt` merge and the real referee
`oracle.oracle`. Every run reproduces exactly from `(seed, scenario)`:

    python -m chaos.run --seeds 0:500 --scenario all
    python -m chaos.run --seed 17 --scenario net_restore_old_backup --trace
"""
