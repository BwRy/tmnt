# coding: utf-8
"""
Copyright (c) 2019 The MITRE Corporation.
"""

import json
import mxnet as mx
import numpy as np
import gluonnlp as nlp
import io
import os
from tmnt.bow_vae.bow_models import BowNTM, MetaDataBowNTM
from tmnt.bow_vae.bow_doc_loader import collect_stream_as_sparse_matrix, DataIterLoader, BowDataSet, file_to_sp_vec
from tmnt.preprocess.tokenizer import BasicTokenizer
from multiprocessing import Pool


class BowNTMInference(object):

    def __init__(self, param_file=None, specs_file=None, vocab_file=None, model_dir=None, ctx=mx.cpu()):
        self.max_batch_size = 2
        if model_dir is not None:
            param_file = os.path.join(model_dir,'model.params')
            vocab_file = os.path.join(model_dir,'vocab.json')
            specs_file = os.path.join(model_dir,'model.config')
        with open(specs_file) as f:
            specs = json.loads(f.read())
        with open(vocab_file) as f:
            voc_js = f.read()
        self.vocab = nlp.Vocab.from_json(voc_js)
        self.ctx = ctx
        self.n_latent = specs['n_latent']
        enc_dim = specs['enc_hidden_dim']
        lat_distrib = specs['latent_distribution']
        n_encoding_layers = specs.get('num_enc_layers', 0)
        enc_dr= float(specs.get('enc_dr', 0.0))
        emb_size = specs['embedding_size']
        l1_tgt_sparsty = float(specs.get('target_sparsity', 0.0))
        coherence_reg_penalty = float(specs.get('coherence_regularizer_penalty', 0.0))
        if 'n_covars' in specs:
            self.covar_model = True
            self.n_covars = specs['n_covars']
            self.label_map = specs['l_map']
            self.covar_net_layers = specs.get('covar_net_layers')

            self.model = MetaDataBowNTM(self.label_map, self.n_covars,
                                        self.vocab, enc_dim, self.n_latent, emb_size, latent_distrib=lat_distrib,
                                        n_encoding_layers=n_encoding_layers, enc_dr=enc_dr,                                        
                                        covar_net_layers = self.covar_net_layers, ctx=ctx)
        else:
            self.covar_model = False
            self.model = BowNTM(self.vocab, enc_dim, self.n_latent, emb_size, latent_distrib=lat_distrib,
                                n_encoding_layers=n_encoding_layers, enc_dr=enc_dr,
                                ctx=ctx)
        self.model.load_parameters(str(param_file), allow_missing=False)


    def get_model_details(self, sp_vec_file):
        data_csr, _, labels, _ = file_to_sp_vec(sp_vec_file, len(self.vocab))        
        ## 1) K x W matrix of P(term|topic) probabilities
        w = self.model.decoder.collect_params().get('weight').data().transpose() ## (K x W)
        w_pr = mx.nd.softmax(w, axis=1)
        ## 2) D x K matrix over the test data of topic probabilities
        covars = labels if self.covar_model else None
        dt_matrix = self.encode_csr(data_csr, covars, use_probs=True)
        ## 3) D-length vector of document sizes
        doc_lengths = mx.nd.sum(data_csr, axis=1)
        ## 4) vocab (in same order as W columns)
        ## 5) frequency of each word w_i \in W over the test corpus
        term_cnts = mx.nd.sum(data_csr, axis=0)
        return w_pr, dt_matrix, doc_lengths, term_cnts


    def get_pyldavis_details(self, sp_vec_file):
        w_pr, dt_matrix, doc_lengths, term_cnts = self.get_model_details(sp_vec_file)
        d1 = w_pr.asnumpy().tolist()
        d2 = list(map(lambda x: x.asnumpy().tolist(), dt_matrix))
        d3 = doc_lengths.asnumpy().tolist()
        d5 = term_cnts.asnumpy().tolist()
        d4 = list(map(lambda i: self.vocab.idx_to_token[i], range(len(self.vocab.idx_to_token))))
        d = {'topic_term_dists': d1, 'doc_topic_dists': d2, 'doc_lengths': d3, 'vocab': d4, 'term_frequency': d5 }
        return d
    

    def export_full_model_inference_details(self, sp_vec_file, ofile):
        d = self.get_pyldavis_details(sp_vec_file)
        with io.open(ofile, 'w') as fp:
            json.dump(d, fp, sort_keys=True, indent=4)        


    def encode_texts(self, intexts):
        """
        intexts - should be a list of lists of tokens (each token list being a document)
        """
        in_strms = [nlp.data.SimpleDataStream([t]) for t in intexts]
        strm = nlp.data.SimpleDataStream(in_strms)
        return self.encode_text_stream(strm)

    def encode_vec_file(self, sp_vec_file):
        data_csr, _, labels, _ = file_to_sp_vec(sp_vec_file, len(self.vocab))
        return self.encode_csr(data_csr, labels), labels

    def encode_text_stream(self, strm):
        csr, _, _ = collect_stream_as_sparse_matrix(strm, pre_vocab=self.vocab)
        return self.encode_csr(csr,None)

    def encode_csr(self, csr, labels, use_probs=False):
        batch_size = min(csr.shape[0], self.max_batch_size)
        last_batch_size = csr.shape[0] % batch_size
        covars = mx.nd.one_hot(mx.nd.array(labels, dtype='int'), self.n_covars) \
            if self.covar_model and labels[:-last_batch_size] is not None else None
        infer_iter = DataIterLoader(mx.io.NDArrayIter(csr[:-last_batch_size], covars,
                                                      batch_size, last_batch_handle='discard', shuffle=False))
        encodings = []
        for _, (data,labels) in enumerate(infer_iter):
            data = data.as_in_context(self.ctx)
            if self.covar_model and labels is not None:
                labels = labels.as_in_context(self.ctx)
                encs = self.model.encode_data_with_covariates(data, labels)
            else:
                encs = self.model.encode_data(data)
            if use_probs:
                e1 = encs - mx.nd.min(encs, axis=1).expand_dims(1)
                encs = mx.nd.softmax(e1 ** 0.5)
            encodings.extend(encs)
        ## handle the last batch explicitly as NDArrayIter doesn't do that for us
        if last_batch_size > 0:
            data = csr[-last_batch_size:].as_in_context(self.ctx)
            if self.covar_model and labels is not None:
                labels = mx.nd.one_hot(mx.nd.array(labels[-last_batch_size:], dtype='int'), self.n_covars).as_in_context(self.ctx)
                encs = self.model.encode_data_with_covariates(data, labels)
            else:
                encs = self.model.encode_data(data)
            if use_probs:
                #norm = mx.nd.norm(encs, axis=1, keepdims=True)
                e1 = encs - mx.nd.min(encs, axis=1).expand_dims(1)
                encs = mx.nd.softmax(e1 ** 0.5)
            encodings.extend(encs)
        return encodings

    def get_top_k_words_per_topic(self, k):
        w = self.model.decoder.collect_params().get('weight').data()
        sorted_ids = w.argsort(axis=0, is_ascend=False)
        topic_terms = []
        for t in range(self.n_latent):
            top_k = [ self.vocab.idx_to_token[int(i)] for i in list(sorted_ids[:k, t].asnumpy()) ]
            topic_terms.append(top_k)
        return topic_terms

    def get_top_k_words_per_topic_per_covariate(self, k):
        n_topics = self.n_latent
        w = self.model.cov_decoder.cov_inter_decoder.collect_params().get('weight').data()
        n_covars = int(w.shape[1] / n_topics)
        topic_terms = []
        for i in range(n_covars):
            cv_i_slice = w[:, (i * n_topics):((i+1) * n_topics)]
            sorted_ids = cv_i_slice.argsort(axis=0, is_ascend=False)
            cv_i_terms = []
            for t in range(n_topics):
                top_k = [ self.vocab.idx_to_token[int(i)] for i in list(sorted_ids[:k, t].asnumpy()) ]
                cv_i_terms.append(top_k)
            topic_terms.append(cv_i_terms)
        return topic_terms


    def get_top_k_words_per_topic_over_scalar_covariate(self, k, min_v=0.0, max_v=1.0, step=0.1):
        topic_terms = []
        for sc in np.arange(min_v, max_v, step=step):
            cv_i_terms = []
            for t in range(self.n_latent):
                sorted_topic_ids = self.model.get_top_k_terms_with_covar(sc, t)
                top_k = [ self.vocab.idx_to_token[int(i)] for i in list(sorted_topic_ids.asnumpy()) ]
                cv_i_terms.append(top_k)
            topic_terms.append(cv_i_terms)
        return topic_terms


    def _test_inference_on_directory(self, directory, file_pattern=None):
        """
        Temporary test method to demonstrate use of inference on a set of files in a directory
        """
        pat = '*.txt' if file_pattern is None else file_pattern
        dataset_strm = BowDataSet(directory, pat, sampler='sequential') # preserve file ordering
        return self.encode_text_stream(dataset_strm)
        

class TextEncoder(object):

    """
    Takes a batch of text strings/documents and returns a matrix of their encodings (each row in the matrix
    corresponds to the encoding of the corresponding input text).

    Parameters
    ----------
    inference - the inference object using the trained model
    use_probs - boolean that indicates whether raw topic scores should be converted to probabilities or not (default = True)
    pool_size - integer that specifies the number of processes to use for concurrent text pre-processing
    """
    def __init__(self, inference, use_probs=True, temp=0.5):
        self.temp      = temp
        self.inference = inference
        self.use_probs = use_probs
        self.vocab_len = len(self.inference.vocab.idx_to_token)
        self.tokenizer = BasicTokenizer(do_lower_case=True, use_stop_words=False)

    def _txt_to_vec(self, txt):
        toks = self.tokenizer.tokenize(txt)
        ids = [self.inference.vocab[token] for token in toks if token in self.inference.vocab]
        return ids

    def encode_single_string(self, txt):
        data = mx.nd.zeros((1, self.vocab_len))
        for t in self._txt_to_vec(txt):
            data[0][t] += 1.0
        encs = self.inference.model.encode_data(data)
        if self.use_probs:
            e1 = encs - mx.nd.min(encs, axis=1).expand_dims(1)
            encs = mx.nd.softmax(e1 ** 0.5)
        return encs

    def encode_batch(self, txts, covars=None, pool_size=4):
        pool = Pool(pool_size)
        ids  = pool.map(self._txt_to_vec, txts)
        # Prevent zombie threads from hanging around
        pool.terminate()
        pool = None
        data = mx.nd.zeros((len(txts), self.vocab_len))
        for i, txt_ids in enumerate(ids):
            for t in txt_ids:
                data[i][t] += 1.0
        model = self.inference.model
        if covars:
            covar_ids = mx.nd.array([model.label_map[v] for v in covars])
            covars = mx.nd.one_hot(covar_ids, depth=len(model.label_map))
            encs = model.encode_data_with_covariates(data, covars)
        else:
            encs = model.encode_data(data)
        if self.use_probs:
            e1 = encs - mx.nd.min(encs, axis=1).expand_dims(1)
            encs = mx.nd.softmax(e1 ** self.temp)
        return encs
