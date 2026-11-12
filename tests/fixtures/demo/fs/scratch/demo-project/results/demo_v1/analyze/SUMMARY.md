# Demo run: summary

Made-up numbers, to show how a results summary reads in Torch Bench.

## Headline

- **17 of 19** fine-tunes finished; 2 failed (one GPU out-of-memory, one time limit).
- Best validation loss: `0.412` (run_a, step 1800).
- The effect holds in *both* evaluation sets.

## Results

| Run | Steps | Val loss | Accuracy |
| --- | ---: | ---: | ---: |
| run_a | 2000 | 0.412 | 91.3% |
| run_b | 2000 | 0.447 | 89.8% |
| run_c | 1200 | 0.731 | 78.0% |

## How to reproduce

1. Push the code.
2. Submit with the launcher:

```
bash scripts/submit_demo.sh test --dry-run
bash scripts/submit_demo.sh test
```

> Estimates only: this file is demo data.

See the [Torch documentation](https://services.rt.nyu.edu/docs/hpc/getting_started/intro/) for the cluster itself.
