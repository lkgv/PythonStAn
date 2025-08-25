
from pythonstan.world.pipeline import Pipeline
from pythonstan.world import World

TEST_ROOT = '/home/khabir/code/test/'
LIB_PATH = '/usr/lib/python3.10'

CONFIG = {
    "filename": f"{TEST_ROOT}/test.py",
    "project_path": "{TEST_ROOT}",
    "library_paths": [
        f"{LIB_PATH}",
        f"{LIB_PATH}/site-packages"
        # "/home/khbair/anaconda3/envs/pytorch/lib/python3.9",
        # "/home/khabir/anaconda3/envs/pytorch/lib/python3.9/site-packages"
    ],
    "analysis": [
        {
            "name": "mini_cg",
            "id": "MiniCGAnalysis",
            "description": "mini cg analysis",
            "prev_analysis": ["cfg"],
            "options": {
                "type": "inter-procedure",
                "ir": "cfg"
            }
        },
    ]
}


def main():
    ppl = Pipeline(config=CONFIG)
    ppl.run()
    # print(ppl.analysis_manager.get_results("mini_cg"))


if __name__ == "__main__":
    main()