import scrubvae
from neuroposelib import read
import torch
from neuroposelib import visualization as vis
import numpy as np
from pathlib import Path
from scipy.stats import circvar
import sys

from base_path import RESULTS_PATH

analysis_key = sys.argv[1]
vis_path = RESULTS_PATH + analysis_key + "/vis_latents/"
config = read.config(RESULTS_PATH + analysis_key + "/model_config.yaml")

dataset_label = "Train"
loader = scrubvae.get.mouse_data(
    data_config=config["data"],
    window=config["model"]["window"],
    train=dataset_label == "Train",
    data_keys=["x6d", "root", "heading", "avg_speed"],
    shuffle=False,
)
k_pred = np.load(vis_path + "z_{}_gmm.npy".format(sys.argv[2]))

for key in ["heading"]:
    # k_pred_null = np.load(vis_path + "z_{}_gmm.npy".format(key))
    if key == "heading":
        heading = loader.dataset[:]["heading"].cpu().detach().numpy()
        feat = np.arctan2(heading[:, 0], heading[:, 1])
    else:
        feat = loader.dataset[:][key].cpu().detach().numpy().squeeze()

    z_cvar, z_null_cvar = [], []
    for i in range(25):
        z_cvar += [circvar(feat[k_pred == i], high=np.pi, low=-np.pi)]
        # z_null_cvar += [circvar(feat[k_pred_null == i], high=np.pi, low=-np.pi)]

    print(np.mean(z_cvar))

    scrubvae.plot.feature_ridge(
        feature=feat,
        labels=k_pred,
        xlabel=key,
        ylabel="Cluster",
        x_lim=(feat.min() - 0.1, feat.max() + 0.1),
        n_bins=200,
        binrange=(feat.min(), feat.max()),
        path="{}{}_".format(vis_path, key),
    )

    # scrubvae.plot.feature_ridge(
    #     feature=feat,
    #     labels=k_pred_null,
    #     xlabel=key,
    #     ylabel="Cluster",
    #     x_lim=(feat.min() - 0.1, feat.max() + 0.1),
    #     binrange=(feat.min(), feat.max()),
    #     n_bins=200,
    #     path="{}{}_null_".format(vis_path, key),
    # )
