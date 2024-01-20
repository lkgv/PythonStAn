import ast
from pythonstan.world.pipeline import Pipeline
from pythonstan.world import World

CONFIG = {
    "filename": "/home/yanggw2022/code/test/test.py",
    "project_path": "/home/yanggw2022/code/test",
    "library_paths": [
        "/home/yanggw2022/anaconda3/envs/pytorch/lib/python3.9",
        "/home/yanggw2022/anaconda3/envs/pytorch/lib/python3.9/site-packages"
    ],
    'analysis': []
}

'''
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
'''


def main():
    ppl = Pipeline(config=CONFIG)
    ppl.run()
    print('\n'.join([ast.unparse(x) for x in ppl.get_world().scope_manager.get_ir(ppl.get_world().entry_module, 'three address form').body]))
    for scope in ppl.get_world().scope_manager.get_scopes():
        print(f'<Scope: {scope.get_qualname()}>')
        print('\n'.join([str(x) for x in ppl.get_world().scope_manager.get_ir(scope, 'ir')]))
        print()
    print(ppl.get_world().scope_manager.get_scopes())
    # print(ppl.analysis_manager.get_results("ir"))


if __name__ == "__main__":
    main()