import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

from src.common.tools import is_combination_to_do

def plot_solving_times(
        master_result_df: pd.DataFrame, subproblem_result_df: pd.DataFrame,
        results_path: Path, config):
    
    master_result_df = master_result_df[['config', 'group', 'instance', 'iteration',
        'master_time', 'cache_time', 'iteration_tracked_elapsed_time']]
    
    for key, master_iterations in master_result_df.groupby(['config', 'group', 'instance']):

        if not is_combination_to_do(key[0], key[1], key[2], config):
            continue

        b = subproblem_result_df[
            (subproblem_result_df['config'] == key[0]) &
            (subproblem_result_df['group'] == key[1]) &
            (subproblem_result_df['instance'] == key[2])]
        b: pd.DataFrame = b.rename(columns={'time': 'subproblem_time'})
        a = master_iterations[['iteration', 'master_time', 'cache_time', 'iteration_tracked_elapsed_time']]

        subproblem_total_by_iteration = b[['iteration', 'subproblem_time']].groupby('iteration').sum()
        k = pd.merge(a, subproblem_total_by_iteration, on='iteration', how='left').set_index('iteration').sort_index()
        k['subproblem_time'] = pd.to_numeric(k['subproblem_time'], errors='coerce').fillna(0.0)
        k['iteration_tracked_elapsed_time'] = pd.to_numeric(k['iteration_tracked_elapsed_time'], errors='coerce').fillna(
            k['master_time'].fillna(0.0) + k['cache_time'].fillna(0.0) + k['subproblem_time'].fillna(0.0))
        k['other_tracked_time'] = (
            k['iteration_tracked_elapsed_time'].fillna(0.0)
            - k['master_time'].fillna(0.0)
            - k['cache_time'].fillna(0.0)
            - k['subproblem_time'].fillna(0.0)
        ).clip(lower=0.0)

        fig, ax = plt.subplots()

        if 'cache_time' in a:
            cache_data = a[['iteration', 'cache_time']].dropna().sort_values(by='iteration')
            ax.plot(cache_data['iteration'], cache_data['cache_time'], color='red', marker='s', markeredgecolor='white', label='cache')
        
        ax.plot(k.index, k['master_time'], color='blue', marker='o', markeredgecolor='white', label='master')
        ax.plot(k.index, k['subproblem_time'], color='green', marker='.', markeredgecolor='white', label='total subproblem')
        ax.plot(k.index, k['other_tracked_time'], color='purple', marker='d', markeredgecolor='white', label='other tracked')

        cmin = b[['iteration', 'subproblem_time']].groupby('iteration').min()
        cmax = b[['iteration', 'subproblem_time']].groupby('iteration').max()
        caverage = b[['iteration', 'subproblem_time']].groupby('iteration').agg('mean')
        ax.errorbar(caverage.index, caverage['subproblem_time'], yerr=(cmin['subproblem_time'], cmax['subproblem_time']), label='subproblems', color='orange', capsize=2)
        
        ax.legend()
        ax.set_xlabel('Iteration')
        ax.set_ylabel('Time (s)')
        ax.set_title(f'Solving times of config \'{key[0]}\'\ngroup \'{key[1]}\' instance \'{key[2]}\'')

        save_path = results_path.joinpath(f'{key[0]}__{key[1]}__{key[2]}', 'plots')
        save_path.mkdir(exist_ok=True)

        fig.savefig(save_path.joinpath('solving_times.png'))
        plt.close('all')
