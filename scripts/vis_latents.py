import ssumo
from torch.utils.data import DataLoader
from neuroposelib import read
import matplotlib.pyplot as plt
import torch
from neuroposelib import visualization as vis
import numpy as np
from pathlib import Path
import sys
from base_path import RESULTS_PATH

z_null = None
gen_means_cluster = False
gen_samples_cluster = False
gen_actions = False
vis_clusters = True

analysis_key = sys.argv[1]
vis_path = RESULTS_PATH + analysis_key + "/vis_latents/"
config = read.config(RESULTS_PATH + analysis_key + "/model_config.yaml")
k = 50  # Number of clusters
Path(vis_path).mkdir(parents=True, exist_ok=True)

connectivity = read.connectivity_config(config["data"]["skeleton_path"])
dataset_label = "Test"
### Load Datasets
loader, model = ssumo.get.data_and_model(
    config,
    load_model=config["out_path"],
    epoch=sys.argv[2],
    dataset_label=dataset_label,
    data_keys=["x6d", "root", "offsets", "raw_pose", "target_pose"] + config["disentangle"]["features"],
    shuffle=False,
    verbose=0,
)

latents = ssumo.get.latents(
    config, model, sys.argv[2], loader, device="cuda", dataset_label=dataset_label
)

if z_null is not None:
    print("Projecting latents to decoder null space")
    latents = ssumo.eval.project_to_null(
        latents, model.disentangle[z_null].decoder.weight.cpu().detach().numpy()
    )[0]

# if "avg_speed_3d" in config["disentangle"]["features"]:
#     print("Found avg_speed_3d in disentanglement keys. Appending to latents.")
#     latents = np.concatenate([latents, loader.dataset[:]["avg_speed_3d"].numpy()],axis=-1)

if "mcmi" in config["disentangle"]["method"].keys():
    from sklearn.decomposition import PCA
    pca = PCA().fit(latents)
    print(np.cumsum(pca.explained_variance_ratio_))
    # import pdb; pdb.set_trace()
#     exp_var_99 = np.where(np.cumsum(pca.explained_variance_ratio_)>0.95)[0][1]
#     latents = pca.transform(latents)[:,:exp_var_99]

### Visualize clusters
if vis_clusters:
    label = "z{}".format("" if z_null is None else "_" + z_null)
    k_pred, gmm = ssumo.eval.cluster.gmm(
        latents=latents,
        n_components=k,
        label=label + "_{}".format(sys.argv[2]),
        path=vis_path,
        covariance_type="diag",
    )

    print(
        np.histogram(
            k_pred, bins=len(np.unique(k_pred)), range=(-0.5, np.max(k_pred) + 0.5)
        )[0]
    )

    ssumo.plot.sample_clusters(
        loader.dataset[:]["raw_pose"].detach().cpu().numpy(),
        k_pred,
        connectivity,
        "{}/vis_clusters_{}{}/".format(vis_path, "" if z_null is None else z_null,sys.argv[2]),
    )

    f = plt.figure(figsize=(10,5))
    plt.hist(k_pred, bins=len(np.unique(k_pred)), range=(-0.5, k - 0.5))
    plt.xlabel("GMM Cluster")
    plt.ylabel("# Actions")
    plt.savefig("{}/vis_clusters_{}/gmm_hist.png".format(vis_path, sys.argv[2]))
    plt.close()


# mean_offsets = dataset.data["offsets"].mean(axis=(0, -1)).cuda()
# latent_means = latents.mean(axis=0)
# latent_std = latents.std(axis=0)
# num_latents = latents.shape[-1]
# if gen_means_cluster:
#     k_pred, gmm = utils.get_gmm_clusters(latents, k, label="cluster", path=vis_path)
#     assert len(k_pred) == len(dataset)
#     gmm_means = torch.tensor(gmm.means_, dtype=torch.float32)
#     eps = torch.randn_like(gmm_means)
#     gmm_L = torch.linalg.cholesky(torch.tensor(gmm.covariances_)).type(torch.float32)
#     gmm_gen = torch.matmul(gmm_L, eps[..., None]).squeeze().add_(gmm_means)
#     # import pdb; pdb.set_trace()
#     x_o = model.decoder(gmm_gen.cuda()).moveaxis(-1, 1)

#     if config["arena_size"] is None:
#         x6d_o = x_o.reshape((k * config["window"], -1, 6))
#     else:
#         x6d_o = x_o[..., :-3].reshape(-1, dataset.n_keypts, 6)
#         root_o = inv_normalize_root(x_o[..., -3:], arena_size).reshape(-1, 3)

#     pose = fwd_kin_cont6d_torch(
#         x6d_o,
#         dataset.kinematic_tree,
#         (mean_offsets[:, None] * dataset.offset.cuda()).repeat(
#             k * config["window"], 1, 1
#         ),
#         root_o,
#         do_root_R=True,
#     )

#     vis.pose.grid3D(
#         pose.cpu().detach().numpy(),
#         connectivity,
#         frames=np.arange(k) * config["window"],
#         centered=False,
#         subtitles=["GMM Cluster: {}".format(i) for i in range(k)],
#         title="Mean Sample from GMM Clusters",
#         fps=45,
#         N_FRAMES=config["window"],
#         VID_NAME="cluster.mp4",
#         SAVE_ROOT=vis_path + "/gen_clips_means/",
#     )

# if gen_samples_cluster:
#     k_pred, gmm = get_gmm_clusters(latents, k, label="cluster", path=vis_path)
#     assert len(k_pred) == len(dataset)

#     gmm_means = torch.tensor(gmm.means_, dtype=torch.float32)
#     import pdb

#     pdb.set_trace()

# #### Generate actions modifying 1 latent dimension at a time
# def adjust_single_dim(
#     base_latent, latent_means, latent_std, model, window, mean_offsets, out_path
# ):
#     for i in np.where(latent_std > 0.1)[0]:
#         # We take the mean latent of the dataset
#         gen_latent = torch.tensor(base_latent, dtype=torch.float32).repeat(3, 1)
#         # Add +/- 1.5 to a latent dimension
#         gen_latent[[0, 2], i] += torch.tensor([-3, 3])

#         synth_rot6d = (
#             model.decoder(gen_latent.cuda()).moveaxis(-1, 1).reshape((3 * window, -1, 6))
#         )

#         pose = fwd_kin_cont6d_torch(
#             synth_rot6d,
#             dataset.kinematic_tree,
#             mean_offsets,
#             torch.zeros((3 * window, 3)),  # root.moveaxis(-1, 1).reshape((-1, 3)),
#             do_root_R=True,
#         )

#         vis.pose.grid3D(
#             pose.cpu().detach().numpy(),
#             connectivity,
#             frames=np.arange(3) * window,
#             centered=False,
#             labels=["-3", "{:.3f}".format(base_latent[i]), "+3"],
#             title="Latent {}: $\mu={:.3f}$, $\sigma={:.3f}$".format(
#                 i, latent_means[i], latent_std[i]
#             ),
#             fps=45,
#             N_FRAMES=config["window"],
#             VID_NAME="latent_{}.mp4".format(i),
#             SAVE_ROOT=out_path,
#         )

# if gen_actions:
#     adjust_single_dim(
#         latent_means,
#         latent_std,
#         model,
#         config["window"],
#         mean_offsets,
#         vis_path + "gen_clips_means/",
#     )
#     adjust_single_dim(
#         latents[1000],
#         latent_means,
#         latent_std,
#         model,
#         config["window"],
#         mean_offsets,
#         vis_path + "gen_clips_1K/",
#     )
