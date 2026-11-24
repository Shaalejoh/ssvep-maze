# Tools

These are the scripts from before the game existed, plus a replay tool I used while tuning it.

| Script | What it's for |
|---|---|
| `replay_session.py` | Runs a recorded game session back through the decision code and checks that it makes the same moves I made live. You can try different settings with `--floor` and `--horizon`. |
| `ssvep_test.py` | My first experiment, one big flickering square. `live` mode just flickers so you can watch the FFT in the OpenBCI GUI, and `record` mode runs trials and analyzes them. |
| `ssvep_4target.py` | Four squares flickering at once, cued one at a time. This was the step between the single square and the game. |
| `analyze_ssvep.py` | The analysis for those two tests: signal-to-noise at each frequency, CCA accuracy for different window lengths, information transfer rate, and a spectrum plot. |

Run them from inside this folder (`cd tools`), because `ssvep_4target.py` imports from
`ssvep_test.py`. For example:

```bash
python replay_session.py ../data/sub-S01/game-20261123-141841
python ssvep_test.py live --freq 12 --windowed
python ssvep_test.py record --channels O1,Oz,O2,Cz
python ssvep_4target.py --board synthetic --windowed
python analyze_ssvep.py ../data/sub-S01/ssvep-20261120-145304
```

The flicker tests are left the way I ran them. The game itself is in `ssvep_maze/`.
