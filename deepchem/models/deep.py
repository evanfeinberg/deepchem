"""
Code for processing the Google vs-datasets using keras.
"""
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import os
import numpy as np
from keras.models import Graph
from keras.models import model_from_json
from keras.layers.core import Dense, Dropout
from keras.optimizers import SGD
from deepchem.models import Model

class KerasModel(Model):
  """
  Abstract base class shared across all Keras models.
  """

  def save(self, out_dir):
    """
    Saves underlying keras model to disk.
    """
    super(KerasModel, self).save(out_dir)
    model = self.get_raw_model()
    filename, _ = os.path.splitext(Model.get_model_filename(out_dir))

    # Note that keras requires the model architecture and weights to be stored
    # separately. A json file is generated that specifies the model architecture.
    # The weights will be stored in an h5 file. The pkl.gz file with store the
    # target name.
    json_filename = "%s.%s" % (filename, "json")
    h5_filename = "%s.%s" % (filename, "h5")
    # Save architecture
    json_string = model.to_json()
    with open(json_filename, "wb") as file_obj:
      file_obj.write(json_string)
    model.save_weights(h5_filename, overwrite=True)

  def load(self, model_dir):
    """
    Load keras multitask DNN from disk.
    """
    filename = Model.get_model_filename(model_dir)
    filename, _ = os.path.splitext(filename)

    json_filename = "%s.%s" % (filename, "json")
    h5_filename = "%s.%s" % (filename, "h5")

    with open(json_filename) as file_obj:
      model = model_from_json(file_obj.read())
    model.load_weights(h5_filename)
    self.raw_model = model

class MultiTaskDNN(KerasModel):
  """
  Model for multitask MLP in keras.
  """
  def __init__(self, task_types, model_params,
               initialize_raw_model=True):
    super(MultiTaskDNN, self).__init__(task_types, model_params,
                                       initialize_raw_model=initialize_raw_model)
    if initialize_raw_model:
      sorted_tasks = sorted(task_types.keys())
      (n_inputs,) = model_params["data_shape"]
      model = Graph()
      model.add_input(name="input", input_shape=(n_inputs,))
      model.add_node(
          Dense(model_params["nb_hidden"], init=model_params["init"],
                activation=model_params["activation"]),
          name="dense", input="input")
      model.add_node(Dropout(model_params["dropout"]),
                     name="dropout",
                     input="dense")
      top_layer = "dropout"
      for ind, task in enumerate(sorted_tasks):
        task_type = task_types[task]
        if task_type == "classification":
          model.add_node(
              Dense(2, init=model_params["init"], activation="softmax"),
              name="dense_head%d" % ind, input=top_layer)
        elif task_type == "regression":
          model.add_node(
              Dense(1, init=model_params["init"]),
              name="dense_head%d" % ind, input=top_layer)
        model.add_output(name="task%d" % ind, input="dense_head%d" % ind)

      loss_dict = {}
      for ind, task in enumerate(sorted_tasks):
        task_type, taskname = task_types[task], "task%d" % ind
        if task_type == "classification":
          loss_dict[taskname] = "binary_crossentropy"
        elif task_type == "regression":
          loss_dict[taskname] = "mean_squared_error"
      sgd = SGD(lr=model_params["learning_rate"],
                decay=model_params["decay"],
                momentum=model_params["momentum"],
                nesterov=model_params["nesterov"])
      model.compile(optimizer=sgd, loss=loss_dict)
      self.raw_model = model

  def get_data_dict(self, X, y=None):
    """Wrap data X in dict for graph computations (Keras graph only for now)."""
    data = {}
    data["input"] = X
    for ind, task in enumerate(sorted(self.task_types.keys())):
      task_type, taskname = self.task_types[task], "task%d" % ind
      if y is not None:
        if task_type == "classification":
          data[taskname] = to_one_hot(y[:, ind])
        elif task_type == "regression":
          data[taskname] = y[:, ind]
    return data

  def get_sample_weight(self, w):
    """Get dictionaries needed to fit models"""
    sample_weight = {}
    for ind in range(len(sorted(self.task_types.keys()))):
      sample_weight["task%d" % ind] = w[:, ind]
    return sample_weight

  def fit_on_batch(self, X, y, w):
    """
    Updates existing model with new information.
    """
    eps = .001
    # Add eps weight to avoid minibatches with zero weight (causes theano to crash).
    w = w + eps * np.ones(np.shape(w))
    data = self.get_data_dict(X, y)
    sample_weight = self.get_sample_weight(w)
    loss = self.raw_model.train_on_batch(data, sample_weight=sample_weight)
    return loss

  def predict_on_batch(self, X):
    """
    Makes predictions on given batch of new data.
    """
    data = self.get_data_dict(X)
    y_pred_dict = self.raw_model.predict_on_batch(data)
    sorted_tasks = sorted(self.task_types.keys())
    nb_samples = np.shape(X)[0]
    nb_tasks = len(sorted_tasks)
    y_pred = np.zeros((nb_samples, nb_tasks))
    for ind, task in enumerate(sorted_tasks):
      task_type = self.task_types[task]
      taskname = "task%d" % ind
      if task_type == "classification":
        # Class probabilities are predicted for classification outputs. Instead,
        # output the most likely class.
        y_pred_task = np.squeeze(np.argmax(y_pred_dict[taskname], axis=1))
      else:
        print("taskname")
        print(taskname)
        print("y_pred_dict.keys()")
        print(y_pred_dict.keys())
        y_pred_task = np.squeeze(y_pred_dict[taskname])
      y_pred[:, ind] = y_pred_task
    y_pred = np.squeeze(y_pred)
    return y_pred

Model.register_model_type(MultiTaskDNN)

class SingleTaskDNN(MultiTaskDNN):
  """
  Abstract base class for different ML models.
  """
  def __init__(self, task_types, model_params, initialize_raw_model=True):
    super(SingleTaskDNN, self).__init__(task_types, model_params,
                                        initialize_raw_model=initialize_raw_model)

Model.register_model_type(SingleTaskDNN)

def to_one_hot(y):
  """Transforms label vector into one-hot encoding.

  Turns y into vector of shape [n_samples, 2] (assuming binary labels).

  y: np.ndarray
    A vector of shape [n_samples, 1]
  """
  n_samples = np.shape(y)[0]
  y_hot = np.zeros((n_samples, 2))
  for index, val in enumerate(y):
    if val == 0:
      y_hot[index] = np.array([1, 0])
    elif val == 1:
      y_hot[index] = np.array([0, 1])
  return y_hot
