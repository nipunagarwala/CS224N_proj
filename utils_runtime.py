import pickle
import os
import random
import numpy as np
from argparse import ArgumentParser
import reader

import tensorflow as tf
from utils_preprocess import *

tf_ver = tf.__version__
SHERLOCK = (str(tf_ver) == '0.12.1')

# for Sherlock
if SHERLOCK:
	DIR_MODIFIER = '/scratch/users/nipuna1'
# for Azure
else:
	DIR_MODIFIER = '/data'

def genWarmStartDataset(data_len, meta_map, music_map, 
			dataFolder=os.path.join(DIR_MODIFIER, 'full_dataset/warmup_dataset/checked')):
	"""
	Generates metadata and music data for the use in warm starting the RNN models

	A file gets sampled from @dataFolder under ./checked file, and gets encoded using
	the 'vocab_map_meta.p' and 'vocab_map_music.p' files under @vocab_dir.

	The first @data_len characters in the music data is returned.
	"""

	oneHotHeaders = ('R', 'M', 'L', 'K_key', 'K_mode')
	otherHeaders = ('len', 'complexity')

	# while loop here, just in case that the file we choose contains characters that
	# does not appear in the original dataset
	while True:
		# pick a random file in dataFolder
		if os.path.isfile(dataFolder):
			abc_file = dataFolder
		else:
			abc_list = os.listdir(dataFolder)
			abc_file = os.path.join(dataFolder, random.choice(abc_list))

		meta,music = loadCleanABC(abc_file)
		if data_len==-1:
			warm_str = music
		else:
			warm_str = music[:data_len-1]

		# start encoding
		meta_enList = []
		music_enList = []
		encodeSuccess = True

		# encode the metadata info
		for header in oneHotHeaders:
			if meta[header] not in meta_map[header]:
				encodeSuccess = False
				break
			else:
				meta_enList.append(meta_map[header][meta[header]])

		for header in otherHeaders:
			meta_enList.append(meta[header])

		# encode music data
		# add the BEGIN token
		music_enList.append(music_map['<start>'])
		for i in range(len(warm_str)):
			c = music[i]
			if c not in music_map:
				encodeSuccess = False
				break
			else:
				music_enList.append(music_map[c])

		if encodeSuccess:
			break

	print '-'*50
	print 'Generating the warm-start sequence...'
	print 'Chose %s to warm-start...' % abc_file
	print 'Meta Data is: %s' % str(meta)
	print 'The associated encoding is: %s' % str(meta_enList)
	print 'Music to warm-start with is: %s' % warm_str
	print 'The associated encoding is: %s' % str(music_enList)
	print '-'*50

	return meta_enList,music_enList


def sample_with_temperature(logits, temperature):
	flattened_logits = logits.flatten()
	unnormalized = np.exp((flattened_logits - np.max(flattened_logits)) / temperature)
	probabilities = unnormalized / float(np.sum(unnormalized))
	sample = np.random.choice(len(probabilities), p=probabilities)
	return sample


def get_checkpoint(args, session, saver):
	# Checkpoint
	found_ckpt = False

	if args.override:
		if tf.gfile.Exists(args.ckpt_dir):
			tf.gfile.DeleteRecursively(args.ckpt_dir)
		tf.gfile.MakeDirs(args.ckpt_dir)

	# check if arags.ckpt_dir is a directory of checkpoints, or the checkpoint itself
	if len(re.findall('model.ckpt-[0-9]+', args.ckpt_dir)) == 0:
		ckpt = tf.train.get_checkpoint_state(args.ckpt_dir)
		if ckpt and ckpt.model_checkpoint_path:
			saver.restore(session, ckpt.model_checkpoint_path)
			i_stopped = int(ckpt.model_checkpoint_path.split('/')[-1].split('-')[-1])
			print "Found checkpoint for epoch ({0})".format(i_stopped)
			found_ckpt = True
		else:
			print('No checkpoint file found!')
			i_stopped = 0
	else:
		saver.restore(session, args.ckpt_dir)
		i_stopped = int(args.ckpt_dir.split('/')[-1].split('-')[-1])
		print "Found checkpoint for epoch ({0})".format(i_stopped)
		found_ckpt = True


	return i_stopped, found_ckpt


def save_checkpoint(args, session, saver, i):
	checkpoint_path = os.path.join(args.ckpt_dir, 'model.ckpt')
	saver.save(session, checkpoint_path, global_step=i)
	# saver.save(session, os.path.join(SUMMARY_DIR,'model.ckpt'), global_step=i)


def encode_meta_batch(meta_vocabulary, meta_batch):
	new_meta_batch = []
	vocab_lengths = [0] + [len(small_vocab) for small_vocab in meta_vocabulary.values()]
	num_values = len(meta_vocabulary.values())

	for meta_data in meta_batch:
		new_meta_data = [meta_data[i] + sum(vocab_lengths[:i+1]) for i in xrange(num_values)]
		new_meta_data = np.append(new_meta_data, meta_data[5:])
		new_meta_batch.append(new_meta_data)
	return new_meta_batch


def create_noise_meta(meta_vocabulary):
	vocab_lengths = [len(small_vocab) for small_vocab in meta_vocabulary.values()]
	noise_meta_batch = [np.random.randint(upper_bound) for upper_bound in vocab_lengths]
	noise_meta_batch += [np.random.randint(10, high=41), np.random.randint(50, high=400)]
	return np.array(noise_meta_batch)


def pack_feed_values(args, input_batch, label_batch, meta_batch,
							initial_state_batch, use_meta_batch, num_encode, num_decode):
	# if (args.train != "sample"):
	#     for i, input_b in enumerate(input_batch):
	#         if input_b.shape[0] != 50:
	#             print "Input batch {0} contains and examples of size {1}".format(i, input_b.shape[0])
	#             input_batch[i] = np.zeros(50)

	#     for j, label_b in enumerate(label_batch):
	#         if label_b.shape[0] != 50:
	#             print "Output batch {0} contains and examples of size {1}".format(j, label_b.shape[0])
	#             label_batch[j] = np.zeros(50)
	packed = []

	input_batch = np.stack(input_batch)
	label_batch = np.stack(label_batch)

	packed = []
	if args.model == 'seq2seq':
		packed += [input_batch.T, label_batch.T, meta_batch, initial_state_batch, use_meta_batch, num_encode, num_decode]
		# + attention?
	elif args.model == 'char':
		packed += [input_batch, label_batch, meta_batch, initial_state_batch, use_meta_batch]
	elif args.model == 'cbow':
		new_label_batch = [d[-1] for d in label_batch]
		packed += [input_batch, new_label_batch]
	elif args.model == 'gan':
		packed += [input_batch, label_batch, meta_batch, initial_state_batch, use_meta_batch]
		# MORE?
	return packed


def parseCommandLine():
	desc = u'{0} [Args] [Options]\nDetailed options -h or --help'.format(__file__)
	parser = ArgumentParser(description=desc)

	print("Parsing Command Line Arguments...")
	requiredModel = parser.add_argument_group('Required Model arguments')
	requiredModel.add_argument('-m', choices = ["seq2seq", "char", "cbow"], type = str,
						dest = 'model', required = True, help = 'Type of model to run')
	requiredTrain = parser.add_argument_group('Required Train/Test arguments')
	requiredTrain.add_argument('-p', choices = ["train", "test", "sample", "dev"], type = str,
						dest = 'train', required = True, help = 'Training or Testing phase to be run')

	requiredTrain.add_argument('-c', type = str, dest = 'set_config',
							   help = 'Set hyperparameters', default='')

	parser.add_argument('-o', dest='override', action="store_true", help='Override the checkpoints')
	parser.add_argument('-e', dest='num_epochs', default=50, type=int, help='Set the number of Epochs')
	parser.add_argument('-ckpt', dest='ckpt_dir', default=DIR_MODIFIER + '/temp_ckpt/', type=str, help='Set the checkpoint directory')
	parser.add_argument('-data', dest='data_dir', default='', type=str, help='Set the data directory')

	args = parser.parse_args()
	return args





if __name__ == "__main__":
	genWarmStartDataset(20)
