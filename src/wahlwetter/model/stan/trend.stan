// Latent daily vote shares as a random walk on the log-ratio scale.
//
// Parties are modelled relative to a reference category (Sonstige), which is
// fixed at zero on the log-ratio scale. A softmax over the resulting vector
// guarantees the shares sum to one by construction, so no normalisation step
// is needed anywhere downstream.
//
// Each poll is placed at its fieldwork midpoint. House effects shift the
// log-ratio before the softmax, so they act multiplicatively on shares rather
// than additively on percentages -- a 1-point house effect on a party at 30%
// is not the same thing as at 3%.
//
// Observations are grouped: a party an institute did not break out has been
// folded into that poll's Sonstige, so the model compares the SUM of those
// latent shares against the single reported value. Nothing is imputed.

data {
  int<lower=1> n_days;
  int<lower=2> n_parties;          // including the reference category
  int<lower=1> n_polls;
  int<lower=1> n_institutes;
  int<lower=1> n_methods;
  int<lower=1> n_obs;              // (poll, reported group) pairs

  array[n_polls] int<lower=1, upper=n_days> poll_day;
  array[n_polls] int<lower=1, upper=n_institutes> poll_institute;
  array[n_polls] int<lower=1, upper=n_methods> poll_method;
  vector<lower=1>[n_polls] poll_n_eff;
  vector<lower=0>[n_polls] poll_rounding_sd;

  array[n_obs] int<lower=1, upper=n_polls> obs_poll;
  vector<lower=0, upper=1>[n_obs] obs_value;
  array[n_obs] row_vector<lower=0, upper=1>[n_parties] obs_mask;

  int<lower=0, upper=1> include_method_effects;
}

transformed data {
  int n_free = n_parties - 1;      // reference party is pinned at zero
}

parameters {
  vector[n_free] z_init;                    // log-ratio on day 1
  matrix[n_days - 1, n_free] z_step;        // standardised innovations
  // Upper bounds are far outside any plausible value; they exist only to stop
  // warmup wandering somewhere softmax() overflows to NaN.
  vector<lower=0, upper=0.5>[n_free] sigma_rw;   // daily random-walk scale

  // House effects, per institute per party, on the log-ratio scale.
  matrix[n_institutes - 1, n_free] house_raw;
  vector<lower=0, upper=2>[n_free] sigma_house;

  // Method effects, only used when include_method_effects is on.
  matrix[n_methods - 1, n_free] method_raw;
  vector<lower=0, upper=2>[n_free] sigma_method;

  // Extra (non-sampling) error, beyond what the sample size implies.
  real<lower=0, upper=0.2> tau;

  // Design effect: the factor by which the true sampling variance differs
  // from simple random sampling at the reported sample size. Institutes
  // publish weighted projections rather than raw proportions, so this is not
  // assumed to be 1 -- it is estimated, which is what makes poll_n_eff an
  // *effective* sample size rather than just the reported one.
  real<lower=0.05, upper=10> design_effect;
}

transformed parameters {
  matrix[n_days, n_free] z;
  matrix[n_institutes, n_free] house;
  matrix[n_methods, n_free] method;

  // Non-centred random walk: much better geometry than sampling z directly.
  z[1] = z_init';
  for (t in 2 : n_days) {
    z[t] = z[t - 1] + z_step[t - 1] .* sigma_rw';
  }

  // Sum-to-zero over institutes, per party. Without this the house effects
  // and the latent level are only identified up to a common shift: every
  // institute could be "2 points high" and the level 2 points low.
  for (k in 1 : n_free) {
    house[1 : (n_institutes - 1), k] = house_raw[, k] * sigma_house[k];
    house[n_institutes, k] = -sum(house[1 : (n_institutes - 1), k]);
  }

  for (k in 1 : n_free) {
    if (include_method_effects && n_methods > 1) {
      method[1 : (n_methods - 1), k] = method_raw[, k] * sigma_method[k];
      method[n_methods, k] = -sum(method[1 : (n_methods - 1), k]);
    } else {
      method[, k] = rep_vector(0, n_methods);
    }
  }
}

model {
  // --- priors -------------------------------------------------------------
  // Log-ratios against a reference near 4%: a party at 30% sits around 2.
  z_init ~ normal(0, 2);
  to_vector(z_step) ~ std_normal();
  // Daily movement. 0.02 on the log-ratio scale is roughly a 2% relative
  // change per day, which over a month is a large but not absurd swing.
  sigma_rw ~ normal(0, 0.02);

  to_vector(house_raw) ~ std_normal();
  sigma_house ~ normal(0, 0.15);

  to_vector(method_raw) ~ std_normal();
  sigma_method ~ normal(0, 0.10);

  // Extra variance on the share scale. 0.02 = two percentage points.
  tau ~ normal(0, 0.02);
  // Centred on simple random sampling, but free to move well away from it.
  design_effect ~ lognormal(0, 0.5);

  // --- likelihood ---------------------------------------------------------
  {
    matrix[n_polls, n_parties] p;
    for (i in 1 : n_polls) {
      vector[n_parties] eta;
      eta[1] = 0;                                   // reference category
      for (k in 1 : n_free) {
        eta[k + 1] = z[poll_day[i], k]
                     + house[poll_institute[i], k]
                     + method[poll_method[i], k];
      }
      p[i] = softmax(eta)';
    }

    for (o in 1 : n_obs) {
      int i = obs_poll[o];
      // Predicted share of the reported group = sum of its members' shares.
      real mu = obs_mask[o] * p[i]';
      // Binomial sampling variance at the group level, plus an estimated
      // non-sampling term, plus the variance introduced by publishing values
      // rounded to a grid.
      real var_sampling = design_effect * mu * (1 - mu) / poll_n_eff[i];
      real sd_total = sqrt(var_sampling
                           + square(tau)
                           + square(poll_rounding_sd[i]));
      obs_value[o] ~ normal(mu, sd_total);
    }
  }
}

generated quantities {
  // Latent shares per day, which is what the site actually plots.
  matrix[n_days, n_parties] share;
  vector[n_obs] log_lik;
  vector[n_obs] y_rep;

  for (t in 1 : n_days) {
    vector[n_parties] eta;
    eta[1] = 0;
    for (k in 1 : n_free) {
      eta[k + 1] = z[t, k];
    }
    share[t] = softmax(eta)';
  }

  {
    matrix[n_polls, n_parties] p;
    for (i in 1 : n_polls) {
      vector[n_parties] eta;
      eta[1] = 0;
      for (k in 1 : n_free) {
        eta[k + 1] = z[poll_day[i], k]
                     + house[poll_institute[i], k]
                     + method[poll_method[i], k];
      }
      p[i] = softmax(eta)';
    }
    for (o in 1 : n_obs) {
      int i = obs_poll[o];
      real mu = obs_mask[o] * p[i]';
      real sd_total = sqrt(design_effect * mu * (1 - mu) / poll_n_eff[i]
                           + square(tau)
                           + square(poll_rounding_sd[i]));
      log_lik[o] = normal_lpdf(obs_value[o] | mu, sd_total);
      y_rep[o] = normal_rng(mu, sd_total);
    }
  }
}
