# How fast it is: the load demo

`sightglass --demo load --open` reads a fake TwinCAT PLC, in a process of its
own, over real ADS: 1000 REAL variables updated 20 times a second, read
every 50 ms in sum reads of 500. The page shows a heat map of every signal
over the last 10 minutes, ten of them as lines, and what it all costs,
measured once a second inside the dashboard's process. 10 to 20 seconds in,
and then every 40 to 80 seconds, one signal glitches for 100 ms; the log
names it and the heat map marks it. It needs the ads extra
(`pip install "sightglass[ads]"`), Linux or macOS, and port 48898 free. To
change the load, run the demo as a module, with the Python that has
sightglass installed:

```bash
python -m sightglass.load --signals 2000 --rate 50 --open
```

From a clone of this repository, put `uv run` in front: `uv run python -m
sightglass.load ...`.

![The load demo: readings of 20,749 values a second at 6% of a core, a heat map of 1000 signals with two glitches marked, ten signals as lines, and a log of the glitches](https://raw.githubusercontent.com/davidson-engineering/sightglass/main/docs/load-demo.png)

Measured on an Apple M5 Pro (macOS 27, Python 3.14), with one page open:

| Load | Values a second | CPU, one core | Worst event-loop delay | PLC to dashboard | Memory |
| --- | --- | --- | --- | --- | --- |
| 1000 signals at 20 Hz | 19,700 | 9–11% | 6 ms | 10 ms | 156 MB |
| 2000 signals at 20 Hz | 40,000 | 10–13% | 3 ms | 12–14 ms | 249 MB |
| 1000 signals at 50 Hz | 50,000 | 16–18% | 5 ms | 6–7 ms | 157 MB |
| 5000 signals at 20 Hz | 98,000–103,000 | 22–23% | 6 ms | 19–21 ms | 555 MB |

Memory is mostly history: 8 bytes per 50 ms slot per signal, kept for 10
minutes. The fake PLC's own CPU isn't counted; a real PLC does that work.

