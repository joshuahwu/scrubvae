from torch.autograd import Function
import torch.nn as nn
import torch
from torch.nn.functional import mse_loss
import numpy as np

class MovingAverageFilter(nn.Module):
    """
    Stores a moving average over streaming minibatches of data
    with an adaptive forgetting factor.
    """

    def __init__(self, nx, classes, lamdiff=1e-2, delta=1e-3):

        self.classes = classes
        # Running averages of means
        self.register_buffer("m1", torch.zeros(len(self.classes), nx))
        self.register_buffer("m2", torch.zeros(len(self.classes), nx))

        # Forgetting factors
        self.register_buffer("lam1", torch.ones(len(self.classes))*0.5)
        self.register_buffer("lam2", self.lam1 + lamdiff)

        # Update parameters for the forgetting factors
        self.delta = delta
        self.lamdiff = lamdiff
    
    def forward(self, mu):
        return 0

    def evaluate_loss(self, x, y):
        """
        Parameters
        ----------
        x : torch.tensor
            (batch_size x num_features) minibatch of data.

        Returns
        -------
        mean_est : torch.tensor
            (num_features,) estimate of the mean
        """
        for i, label in enumerate(self.classes):
            # Empirical mean on minibatch of data.
            xbar = torch.mean(x[y==label], axis=0)

            # See whether m1 or m2 is a closer match
            d1 = torch.linalg.norm(xbar - self.m1[i])
            d2 = torch.linalg.norm(xbar - self.m2[i])

            # Update forgetting factors
            if d1 < d2:
                self.lam1[i] = np.clip(self.lam1[i] - self.delta, 0.0, 1.0)
                self.lam2[i] = self.lam1[i] + self.lamdiff
            else:
                self.lam2[i] = np.clip(self.lam2[i] + self.delta, 0.0, 1.0)
                self.lam1[i] = self.lam2[i] - self.lamdiff

            # Update m1 and m2
            self.m1[i] = (1 - self.lam1[i]) * xbar + self.lam1[i] * self.m1[i]
            self.m2[i] = (1 - self.lam2[i]) * xbar + self.lam2[i] * self.m2[i]

        mean_estimate = 0.5 * (self.m1 + self.m2)
        d = torch.diagonal(mean_estimate[..., None] - mean_estimate[..., None, :], dim1=-1, dim2=-2, offset = 1)

        # Return estimate of mean
        return torch.linalg.norm(d)
    
    def update(self,**kwargs):
        self.m1 = self.m1.detach()
        self.m2 = self.m2.detach()
        return self

class QuadraticDiscriminantFilter(nn.Module):
    """
    Trains a two quadratic binary classifiers with streaming minibatches of data.

    The forgetting rates of the two classifiers are automatically tuned.
    """

    def __init__(self, nx, classes, lamdiff=1e-2, delta=1e-3):
        super().__init__()

        # Running averages of means
        self.classes = classes

        param_names = [
            "m0a",
            "m1a",
            "m0b",
            "m1b",
            "S0a",
            "S1a",
            "S0b",
            "S1b",
        ]
        for label in self.classes:
            for name in param_names:
                if "m" in name:
                    self.register_buffer(
                        "{}_{}".format(name, label),
                        torch.zeros(nx, requires_grad=False)[None, :],
                    )
                elif "S" in name:
                    self.register_buffer(
                        "{}_{}".format(name, label), torch.eye(nx, requires_grad=False)
                    )

            self.register_buffer(
                "lama_{}".format(label), torch.tensor([0.2], requires_grad=False)
            )
            self.register_buffer(
                "lamb_{}".format(label),
                getattr(self, "lama_{}".format(label)) + lamdiff,
            )

        # Update parameters for the forgetting factors
        self.delta = delta
        self.lamdiff = lamdiff

    def forward(self, mu):
        return 0

    def cgll(self, x, m, S):
        """
        Compute Gaussian Log Likelihood
        """
        resids = torch.sum((x - m) * torch.linalg.solve(S, (x - m).T).T, axis=1)
        return -0.5 * (torch.logdet(S) + resids)

    def update(self, x, y):

        for label in self.classes:
            i0 = y != label
            i1 = y == label
            empirical = {}
            # Empirical mean for -1/+1 class labels
            empirical["m0"] = torch.mean(x[i0], axis=0, keepdim=True).detach()
            empirical["m1"] = torch.mean(x[i1], axis=0, keepdim=True).detach()

            # Empirical covariance for -1/+1 class labels
            empirical["S0"] = torch.cov(x[i0].T, correction=0).detach()
            empirical["S1"] = torch.cov(x[i1].T, correction=0).detach()

            # Update classifier A/B, with forgetting factor `lama/b'
            for cl in ["a", "b"]:
                for par in empirical.keys():
                    # (1 - lama/b) * (current moving avg)
                    forgetting = (
                        1 - getattr(self, "lam{}_{}".format(cl, label))
                    ) * getattr(self, "{}{}_{}".format(par, cl, label))

                    updating = (
                        getattr(self, "lam{}_{}".format(cl, label)) * empirical[par]
                    )

                    setattr(
                        self, "{}{}_{}".format(par, cl, label), forgetting + updating
                    )

        return self

    def evaluate_loss(self, x, y, update=True):
        """
        Parameters
        ----------
        x : torch.tensor
            (batch_size x nx) matrix of independent variables.

        y : torch.tensor
            (batch_size) vector of +1/-1 class labels,

        Returns
        -------
        log_likelihood : torch.tensor
            Average log likelihood of the two quadratic decoders.
        """

        ll_loss = 0
        for label in self.classes:
            # Indices for -1/+1 class labels
            i0 = y != label
            i1 = y == label

            # Compute log likelihood score for classifier A
            lla0 = self.cgll(
                x,
                getattr(self, "m0a_{}".format(label)),
                getattr(self, "S0a_{}".format(label)),
            )
            lla1 = self.cgll(
                x,
                getattr(self, "m1a_{}".format(label)),
                getattr(self, "S1a_{}".format(label)),
            )
            lla = torch.sum(i0 * lla0 + i1 * lla1)

            # Compute log likelihood score for classifier B
            llb0 = self.cgll(
                x,
                getattr(self, "m0b_{}".format(label)),
                getattr(self, "S0b_{}".format(label)),
            )
            llb1 = self.cgll(
                x,
                getattr(self, "m1b_{}".format(label)),
                getattr(self, "S1b_{}".format(label)),
            )
            llb = torch.sum(i0 * llb0 + i1 * llb1)

            # If classifier A is better than B, we decrease the forgetting factors
            # by self.delta
            if update and (lla > llb):
                setattr(
                    self,
                    "lama_{}".format(label),
                    torch.clamp(
                        getattr(self, "lama_{}".format(label)) - self.delta, 0.0, 1.0
                    ),
                )
                setattr(
                    self,
                    "lamb_{}".format(label),
                    getattr(self, "lama_{}".format(label)) + self.lamdiff,
                )

            # If classifier B is better than A, we decrease the forgetting factors
            # by self.delta
            elif update:
                setattr(
                    self,
                    "lamb_{}".format(label),
                    torch.clamp(
                        getattr(self, "lamb_{}".format(label)) + self.delta, 0.0, 1.0
                    ),
                )
                setattr(
                    self,
                    "lama_{}".format(label),
                    getattr(self, "lamb_{}".format(label)) - self.lamdiff,
                )

            # Return average log-likelihood ratios of the two linear decoders
            batch_y = (i1 * 2 - 1).float()
            llra = batch_y @ (lla1 - lla0)
            llrb = batch_y @ (llb1 - llb0)

            ll_loss += (llra + llrb) * 0.5

        return ll_loss / len(self.classes)


class MovingAvgLeastSquares(nn.Module):

    def __init__(
        self, nx, ny, lamdiff=1e-1, delta=1e-4, bias=False, polynomial_order=1
    ):
        super().__init__()
        self.bias = bias
        self.polynomial_order = polynomial_order
        nx_poly = 0
        for i in range(1, polynomial_order + 1):
            nx_poly += torch.prod(torch.arange(nx, nx + i)) / torch.prod(
                torch.arange(i) + 1
            )

        nx = int(nx_poly) + self.bias
        print("Moving Avg Least Squares Bias: {}".format(self.bias))
        # Running average of covariances for first linear decoder
        self.register_buffer("Sxx0", torch.eye(nx, requires_grad=False))
        self.register_buffer("Sxy0", torch.zeros(nx, ny, requires_grad=False))

        # Running average of covariances for first linear decoder
        self.register_buffer("Sxx1", torch.eye(nx, requires_grad=False))
        self.register_buffer("Sxy1", torch.zeros(nx, ny, requires_grad=False))

        # Forgetting factor for the first linear decoder
        self.register_buffer("lam0", torch.tensor([0.9], requires_grad=False))

        # Forgetting factor for the second linear decoder
        self.register_buffer("lam1", self.lam0 + lamdiff)

        # Update parameters for the forgetting factors
        self.delta = delta
        self.lamdiff = lamdiff

    def polynomial_expansion(self, x):
        """
        Parameters
        ----------
        x1 : torch.tensor
            (batch_size, num_features) matrix

        x2 : torch.tensor
            (batch_size, num_features) matrix

        Returns
        -------
        Z : torch.tensor
            (batch_size, num_quadratic_features) matrix.

        Note
        -----
        num_quadratic_features = num_features * (num_features + 1) / 2
        """
        x_list = [x]
        idx = torch.arange(x.shape[1], dtype=torch.long, device=x.device)
        for i in range(1, self.polynomial_order):
            C_idx = torch.combinations(idx, i + 1, with_replacement=True)
            x_list += [x[:, C_idx].prod(dim=-1)]

            # batch_size, n_features = x.shape
            # x_einsum = torch.einsum("ij,ik->ijk", x_list[i], x)
            # import pdb; pdb.set_trace()
            # idx = torch.triu_indices(*x_einsum.shape[-2:])
            # x_list += [x_einsum[:, idx[0], idx[1]]]
        return torch.column_stack(x_list)

    def forward(self, x):
        x = self.polynomial_expansion(x)

        if self.bias:
            x = torch.column_stack((x, torch.ones(x.shape[0], 1, device="cuda")))

        # Solve optimal decoder weights (normal equations)
        W0 = torch.linalg.solve(self.Sxx0, self.Sxy0)
        W1 = torch.linalg.solve(self.Sxx1, self.Sxy1)

        # Predicted values for y
        yhat0 = x @ W0
        yhat1 = x @ W1
        return [yhat0, yhat1]

    def update(self, x, y):
        x = self.polynomial_expansion(x)

        if self.bias:
            x = torch.column_stack((x, torch.ones(x.shape[0], 1, device="cuda")))
        xx = (x.T @ x).detach()
        xy = (x.T @ y).detach()
        # Compute moving averages for the next batch of data
        self.Sxx0 = self.lam0 * self.Sxx0 + xx
        self.Sxy0 = self.lam0 * self.Sxy0 + xy
        self.Sxx1 = self.lam1 * self.Sxx1 + xx
        self.Sxy1 = self.lam1 * self.Sxy1 + xy
        return self

    def evaluate_loss(self, yhat0, yhat1, y):
        """
        Parameters
        ----------
        x : torch.tensor
            (batch_size x nx) matrix of independent variables.

        y : torch.tensor
            (batch_size x ny) matrix of dependent variables.

        Returns
        -------
        loss : torch.tensor
            Scalar loss reflecting average mean squared error of the
            two moving average estimates of the linear decoder.
        """
        # Loss for each decoder
        l0 = mse_loss(y, yhat0, reduction="sum")
        l1 = mse_loss(y, yhat1, reduction="sum")

        # If lam0 performed better than lam1, we decrease the forgetting factors
        # by self.delta
        if l0 < l1:
            self.lam0 = torch.clamp(self.lam0 - self.delta, 0.0, 1.0)
            self.lam1 = self.lam0 + self.lamdiff

        # If lam1 performed better than lam0, we increase the forgetting factors
        # by self.delta
        else:
            self.lam1 = torch.clamp(self.lam1 + self.delta, 0.0, 1.0)
            self.lam0 = self.lam1 - self.lamdiff

        # Return average loss of the two linear decoders
        return (l0 + l1) * 0.5


class GradientReversal(Function):
    @staticmethod
    def forward(ctx, x, alpha):
        ctx.save_for_backward(x, alpha)
        return x

    @staticmethod
    def backward(ctx, grad_output):
        grad_input = None
        _, alpha = ctx.saved_tensors
        if ctx.needs_input_grad[0]:
            grad_input = -alpha * grad_output
        return grad_input, None


revgrad = GradientReversal.apply


class GradientReversalLayer(nn.Module):
    def __init__(self, alpha):
        super(GradientReversalLayer, self).__init__()
        self.alpha = torch.tensor(alpha, requires_grad=False)

    def forward(self, x):
        return revgrad(x, self.alpha)


class MLP(nn.Module):
    def __init__(self, in_dim, out_dim):
        super(MLP, self).__init__()
        self.mlp = nn.Sequential(
            nn.Linear(in_dim, in_dim),
            nn.ReLU(),
            nn.Linear(in_dim, in_dim),
            nn.ReLU(),
            nn.Linear(in_dim, out_dim),
        )

    def forward(self, z):
        return self.mlp(z)


class MLPEnsemble(nn.Module):
    def __init__(self, in_dim, out_dim, n_models=3):
        super(MLPEnsemble, self).__init__()
        mlp_list = []
        for i in range(n_models):
            mlp_list += [MLP(in_dim, out_dim)]
        self.ensemble = nn.ModuleList(mlp_list)

    def forward(self, z):
        return [mlp(z) for mlp in self.ensemble]


class ReversalEnsemble(nn.Module):
    def __init__(self, in_dim, out_dim, bound=False):
        super(ReversalEnsemble, self).__init__()

        self.lin = nn.Sequential(
            nn.Linear(in_dim, out_dim),
            nn.Tanh() if bound else None,
        )

        self.mlp1 = nn.Sequential(
            nn.Linear(in_dim, in_dim),
            nn.ReLU(),
            nn.Linear(in_dim, in_dim),
            nn.ReLU(),
            nn.Linear(in_dim, out_dim),
            nn.Tanh() if bound else None,
        )

        self.mlp2 = nn.Sequential(
            nn.Linear(in_dim, in_dim),
            nn.ReLU(),
            nn.Linear(in_dim, out_dim),
            nn.Tanh() if bound else None,
        )

        self.mlp3 = nn.Sequential(
            nn.Linear(in_dim, in_dim),
            nn.ReLU(),
            nn.Linear(in_dim, in_dim // 2),
            nn.ReLU(),
            nn.Linear(in_dim // 2, out_dim),
            nn.Tanh() if bound else None,
        )

    def forward(self, z):
        # a = self.lin(z)
        b = self.mlp1(z)
        c = self.mlp2(z)
        d = self.mlp3(z)
        return [b, c, d]  # a,


class GRScrubber(nn.Module):
    def __init__(self, in_dim, out_dim, alpha=1.0, bound=False):
        super(GRScrubber, self).__init__()
        self.reversal = nn.Sequential(
            GradientReversalLayer(alpha), ReversalEnsemble(in_dim, out_dim, bound)
        )

    def forward(self, z):
        return {"gr": self.reversal(z)}

class LinearProjection(nn.Module):
    def __init__(
        self,
        in_dim,
        out_dim,
        bias=False,
    ):
        super(LinearProjection, self).__init__()
        self.decoder = nn.Linear(in_dim, out_dim, bias=bias)

    def forward(self, z):
        x = self.decoder(z)
        w = self.decoder.weight

        nrm = w @ w.T
        z_null = z - torch.linalg.solve(nrm, x.T).T @ w
        data_o = {"v": x, "z_null": z_null}
        return data_o



class LinearDisentangle(nn.Module):
    def __init__(
        self,
        in_dim,
        out_dim,
        bias=False,
        reversal="linear",
        alpha=1.0,
        do_detach=True,
        n_models=None,
    ):
        super(LinearDisentangle, self).__init__()
        self.do_detach = do_detach

        self.decoder = nn.Linear(in_dim, out_dim, bias=bias)
        if reversal == "mlp":
            self.reversal = nn.Sequential(
                GradientReversalLayer(alpha),
                nn.Linear(in_dim, in_dim),
                nn.ReLU(),
                nn.Linear(in_dim, in_dim),
                nn.ReLU(),
                nn.Linear(in_dim, out_dim),
            )
        elif reversal == "linear":
            self.reversal = nn.Sequential(
                GradientReversalLayer(alpha), nn.Linear(in_dim, out_dim, bias=True)
            )
        elif reversal == "ensemble":
            if (n_models == None) or (n_models == 0):
                self.reversal = nn.Sequential(
                    GradientReversalLayer(alpha), ReversalEnsemble(in_dim, out_dim)
                )
            else:
                self.reversal = nn.Sequential(
                    GradientReversalLayer(alpha), MLPEnsemble(in_dim, out_dim, n_models)
                )
        else:
            self.reversal = None

    def forward(self, z):
        x = self.decoder(z)
        w = self.decoder.weight

        nrm = w @ w.T
        z_null = z - torch.linalg.solve(nrm, x.T).T @ w

        data_o = {"v": x, "mu_null": z_null}

        if self.reversal is not None:
            data_o["gr"] = self.reversal(z_null)

        return data_o
