"""The joint engine moved to 第七章_Borden联合应用/borden3d_linked.py.
Standalone chapters use borden3d_standalone.py. Importing this compatibility
guard cannot silently connect standalone experiments to chapter-7 inputs.
"""
def __getattr__(name):
    raise ImportError('Joint code is isolated in 第七章_Borden联合应用; use run_chapter7.py')
