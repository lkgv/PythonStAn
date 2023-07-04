from pythonstan.world.pipeline import Pipeline
from pythonstan.world import World

CONFIG = {
    "filename": "/home/yanggw2022/code/test/test.py",
    "project_path": "/home/yanggw2022/code/test",
    "library_paths": [
        "/home/yanggw2022/anaconda3/envs/pytorch/lib/python3.9",
        "/home/yanggw2022/anaconda3/envs/pytorch/lib/python3.9/site-packages"
    ],
    "analysis": [
        {
            "name": "liveness",
            "id": "LivenessAnalysis",
            "description": "liveness analysis",
            "prev_analysis": ["cfg"],
            "options": {
                "type": "dataflow analysis",
                "ir": "ssa"
            }
        },
    ]
}


def main():
    ppl = Pipeline(config=CONFIG)
    ppl.run()
    print(ppl.analysis_manager.get_results("liveness"))


if __name__ == "__main__":
    main()