# coding: utf-8

import os, sys
import argparse

from tmnt.embeddings.train_embeddings import train


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Word embedding training with Gluon.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    # Data options
    group = parser.add_argument_group('Data arguments')
    group.add_argument('--data_type', type=str, default='twitter', help='Corpus type (twitter, news, etc.) - triggers appropriate pre-processing')
    group.add_argument('--data_root', type=str, default='',
                       help='Training dataset.')
    group.add_argument('--file_pattern', type=str, default='*.txt', help='File regex pattern to select files in corpus (relative to data_root)')
    group.add_argument('--wiki-root', type=str, default='text8',
                       help='Root under which preprocessed wiki dump.')
    group.add_argument('--wiki-language', type=str, default='text8',
                       help='Language of wiki dump.')
    group.add_argument('--wiki-date', help='Date of wiki dump.')

    # Computation options
    group = parser.add_argument_group('Computation arguments')
    group.add_argument('--batch-size', type=int, default=1024,
                       help='Batch size for training.')
    group.add_argument('--epochs', type=int, default=5, help='Epoch limit')
    group.add_argument(
        '--gpu', type=int, nargs='+',
        help=('Number (index) of GPU to run on, e.g. 0. '
              'If not specified, uses CPU.'))
    group.add_argument('--no-prefetch-batch', action='store_true',
                       help='Disable multi-threaded nogil batch prefetching.')
    group.add_argument('--num-prefetch-epoch', type=int, default=3,
                       help='Start data pipeline for next N epochs when beginning current epoch.')
    group.add_argument('--no-hybridize', action='store_true',
                       help='Disable hybridization of gluon HybridBlocks.')

    # Model
    group = parser.add_argument_group('Model arguments')
    group.add_argument('--emsize', type=int, default=300,
                       help='Size of embedding vectors.')
    group.add_argument('--ngrams', type=int, nargs='+', default=[3, 4, 5, 6])
    group.add_argument(
        '--ngram-buckets', type=int, default=2000000,
        help='Size of word_context set of the ngram hash function. '
        'Set this to 0 for Word2Vec style training.')
    group.add_argument('--model', type=str, default='skipgram',
                       help='SkipGram or CBOW.')
    group.add_argument('--window', type=int, default=5,
                       help='Context window size.')
    group.add_argument(
        '--negative', type=int, default=5, help='Number of negative samples '
        'per source-context word pair.')
    group.add_argument('--frequent-token-subsampling', type=float,
                       default=1E-4,
                       help='Frequent token subsampling constant.')
    group.add_argument(
        '--max-vocab-size', type=int,
        help='Limit the number of words considered. '
        'OOV words will be ignored.')

    # Optimization options
    group = parser.add_argument_group('Optimization arguments')
    group.add_argument('--optimizer', type=str, default='groupadagrad')
    group.add_argument('--lr', type=float, default=0.1)
    group.add_argument('--seed', type=int, default=1, help='random seed')

    # Logging
    group = parser.add_argument_group('Logging arguments')
    group.add_argument('--logdir', type=str, default='logs',
                       help='Directory to store logs.')
    group.add_argument('--log-interval', type=int, default=100)
    group.add_argument(
        '--eval-interval', type=int,
        help='Evaluate every --eval-interval iterations '
        'in addition to at the end of every epoch.')
    group.add_argument('--no-eval-analogy', action='store_true',
                       help='Don\'t evaluate on the analogy task.')

    args_ = parser.parse_args()

    return args_


if __name__ == '__main__':
    args = parse_args()
    train(args)