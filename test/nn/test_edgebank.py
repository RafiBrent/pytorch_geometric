import argparse
import math
import os
import os.path as osp
import sys
import timeit
from pathlib import Path

import pytest
import torch
from modules.edgebank_predictor import EdgeBankPredictor
from sklearn.metrics import average_precision_score, roc_auc_score
from tgb.linkproppred.dataset import LinkPropPredDataset
# internal imports
from tgb.linkproppred.evaluate import Evaluator
from tgb.utils.utils import save_results, set_random_seed
from tqdm import tqdm

from torch_geometric.loader import TemporalDataLoader


# ==================
# ==================
# ==================
def main_test():
    def helper_func(data, test_mask, neg_sampler, split_mode):
        r"""
        Evaluated the dynamic link prediction
        Evaluation happens as 'one vs. many', meaning that each positive edge is evaluated against many negative edges

        Parameters:
            data: a dataset object
            test_mask: required masks to load the test set edges
            neg_sampler: an object that gives the negative edges corresponding to each positive edge
            split_mode: specifies whether it is the 'validation' or 'test' set to correctly load the negatives
        Returns:
            perf_metric: the result of the performance evaluaiton
        """
        num_batches = math.ceil(len(data['sources'][test_mask]) / BATCH_SIZE)
        perf_list = []
        for batch_idx in tqdm(range(num_batches)):
            start_idx = batch_idx * BATCH_SIZE
            end_idx = min(start_idx + BATCH_SIZE,
                          len(data['sources'][test_mask]))
            pos_src, pos_dst, pos_t = (
                data['sources'][test_mask][start_idx:end_idx],
                data['destinations'][test_mask][start_idx:end_idx],
                data['timestamps'][test_mask][start_idx:end_idx],
            )
            neg_batch_list = neg_sampler.query_batch(pos_src, pos_dst, pos_t,
                                                     split_mode=split_mode)

            for idx, neg_batch in enumerate(neg_batch_list):
                query_src = torch.tensor(
                    [int(pos_src[idx]) for _ in range(len(neg_batch) + 1)])
                query_dst = torch.cat(
                    [torch.tensor([int(pos_dst[idx])]), neg_batch])

                y_pred = edgebank.predict_link(query_src, query_dst)
                # compute MRR
                input_dict = {
                    "y_pred_pos": torch.tensor([y_pred[0]]),
                    "y_pred_neg": torch.tensor(y_pred[1:]),
                    "eval_metric": [metric],
                }
                perf_list.append(evaluator.eval(input_dict)[metric])

            # update edgebank memory after each positive batch
            edgebank.update_memory(pos_src, pos_dst, pos_t)

        perf_metrics = float(torch.mean(perf_list))

        return perf_metrics

    data = 'tgbl-coin'
    bs = 200
    k_value = 10
    seed = 1
    mem_mode = "unlimited"
    time_window_ratio = .15

    # ==================
    # ==================
    # ==================

    start_overall = timeit.default_timer()

    # set hyperparameters
    args, _ = get_args()

    SEED = args.seed  # set the random seed for consistency
    set_random_seed(SEED)
    MEMORY_MODE = args.mem_mode  # `unlimited` or `fixed_time_window`
    BATCH_SIZE = args.bs
    K_VALUE = args.k_value
    TIME_WINDOW_RATIO = args.time_window_ratio
    DATA = "tgbl-coin"

    MODEL_NAME = 'EdgeBank'

    # data loading with `numpy`
    dataset = LinkPropPredDataset(name=DATA, root="datasets", preprocess=True)
    data = dataset.full_data
    metric = dataset.eval_metric

    # get masks
    train_mask = dataset.train_mask
    val_mask = dataset.val_mask
    test_mask = dataset.test_mask

    #data for memory in edgebank
    hist_src = torch.cat([data['sources'][train_mask]])
    hist_dst = torch.cat([data['destinations'][train_mask]])
    hist_ts = torch.cat([data['timestamps'][train_mask]])

    # Set EdgeBank with memory updater
    edgebank = EdgeBankPredictor(hist_src, hist_dst, hist_ts,
                                 memory_mode=MEMORY_MODE,
                                 time_window_ratio=TIME_WINDOW_RATIO)

    print("==========================================================")
    print(
        f"============*** {MODEL_NAME}: {MEMORY_MODE}: {DATA} ***=============="
    )
    print("==========================================================")

    evaluator = Evaluator(name=DATA)
    neg_sampler = dataset.negative_sampler

    # ==================================================== Test
    # loading the validation negative samples
    dataset.load_val_ns()

    # testing ...
    start_val = timeit.default_timer()
    perf_metric_test = helper_func(data, val_mask, neg_sampler,
                                   split_mode='val')
    end_val = timeit.default_timer()

    print(f"INFO: val: Evaluation Setting: >>> ONE-VS-MANY <<< ")
    print(f"\tval: {metric}: {perf_metric_test: .4f}")
    test_time = timeit.default_timer() - start_val
    print(f"\tval: Elapsed Time (s): {test_time: .4f}")

    # ==================================================== Test
    # loading the test negative samples
    dataset.load_test_ns()

    # testing ...
    start_test = timeit.default_timer()
    perf_metric_test = test(data, test_mask, neg_sampler, split_mode='test')
    end_test = timeit.default_timer()

    print(f"INFO: Test: Evaluation Setting: >>> ONE-VS-MANY <<< ")
    print(f"\tTest: {metric}: {perf_metric_test: .4f}")
    test_time = timeit.default_timer() - start_test
    print(f"\tTest: Elapsed Time (s): {test_time: .4f}")