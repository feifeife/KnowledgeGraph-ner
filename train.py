import torch
import torch.autograd as autograd
import torch.nn as nn
import data_process
from sklearn.model_selection import train_test_split
import torch
import torch.nn.functional as F
import numpy as np
import time
import os
from word_embedding import load_embeddings
from bilstm_crf import Bilstm_crf
from config import DefaultConfig
START_TAG = "<START>"
STOP_TAG = "<STOP>"

def clip_gradient(model, clip_value):
    params = list(filter(lambda p: p.grad is not None, model.parameters()))
    for p in params:
        p.grad.data.clamp_(-clip_value, clip_value)
def eval_model(model, x_valid,y_valid):
    valid_data = data_process.batch_iter(x_valid, y_valid, opt.batch_size, num_epochs=1)
    total_epoch_loss, total_epoch_acc, total_epoch_pre, total_epoch_recall, total_epoch_f1 = 0, 0, 0, 0, 0
    model.eval()
    for bat_num,valid_batch in enumerate(valid_data):
        text_lengths_val = [len(x) for x in valid_batch[0]]
        max_len_val=max(text_lengths_val)
        pad_token_val= 0
        padded_text_val = np.ones((len(valid_batch[0]), max_len_val)) * pad_token_val
        for idx, t_len in enumerate(text_lengths_val):
            padded_text_val[idx, 0:t_len] = valid_batch[0][idx][:t_len]
        padded_text_val = torch.from_numpy(padded_text_val).long()
        target_val = torch.from_numpy(valid_batch[1]).long()
        target_val = torch.autograd.Variable(target_val).long()
        if torch.cuda.is_available():
            padded_text_val = padded_text_val.cuda()
            target_val = target_val.cuda()
        prediction_val = model(padded_text_val, text_lengths_val)
        loss_val = model.loss(padded_text_val, target_val)
        """ evaluation :acc,precision,recall,f1"""
        num_corrects_val = (torch.max(prediction_val, 1)[1].view(target_val.size()).data == target_val.data).float().sum()
        pre_p = (torch.max(prediction_val, 1)[1].view(target_val.size()).data == torch.ones_like(target_val).data).float().sum()
        actual_p = (target_val.data == torch.ones_like(target_val).data).float().sum()
        true_p = torch.mul(torch.max(prediction_val, 1)[1].view(target_val.size()),target_val).float().sum()
        acc_val = num_corrects_val / len(valid_batch[0])
        precision_val = true_p/pre_p
        recall_val = true_p/actual_p
        f1_val = 2*precision_val*recall_val/(precision_val+recall_val)
        out_val = F.softmax(prediction_val, 1)
        """ 样本数据属于正例概率 """
        logit_val = torch.index_select(out_val.cpu(), 1, torch.LongTensor([1])).squeeze(1).data
        total_epoch_loss += loss_val.item()
        total_epoch_acc += acc_val.item()
        total_epoch_pre += precision_val.item()
        total_epoch_recall += recall_val.item()
        total_epoch_f1 += f1_val.item()
        print(f"validation in batch:{bat_num+1}\n")
        print(torch.max(prediction_val, 1)[1].view(target_val.size()).data)
        print(target_val.data)

    model.train()
    return total_epoch_loss/(bat_num+1), total_epoch_acc/(bat_num+1), total_epoch_pre/(bat_num+1), total_epoch_recall/(bat_num+1), total_epoch_f1/(bat_num+1),logit_val

if __name__ == '__main__':
    opt = DefaultConfig()
    # 数据文件路径
    data_path = opt.data_path
    data = data_process.read_corpus(data_path)
    timestamp = str(int(time.time()))
    outdir = os.path.abspath(os.path.join(os.path.curdir, "checkpoints", timestamp))
    if not os.path.exists(outdir):
        os.makedirs(outdir)

    # print("load embedding dict\n")
    # emb_path = opt.emb_path
    # words, embeddings = load_embeddings(emb_path)



    # print("embedding augment")
    # for sent,tag in data:
    #     for word in sent:
    #         if not word in words:
    #             words.append(word)
    #             embeddings.append([0 for _ in embeddings[0]])
    # words.insert(0, "<pad>")
    # embeddings.insert(0, [0 for _ in embeddings[0]])
    # embeddings = np.array(embeddings)

    # np.save('emb.npy',embeddings)
    embeddings = np.load('emb.npy')
    # np.save('words.npy', np.array(words))
    words = np.load('words.npy')
    words_dict = dict(zip(words,range(len(words))))
    tag2label = {"O": 0,
                 "B-PER": 1, "I-PER": 2,
                 "B-LOC": 3, "I-LOC": 4,
                 "B-ORG": 5, "I-ORG": 6
                 }
    tag2label[START_TAG] = len(tag2label)
    tag2label[STOP_TAG] = len(tag2label)
    print("映射word and tag to id")
    sentence = [[words_dict[word] for word in x[0]]for x in data]
    tags = [[tag2label[tag] for tag in x[1]]for x in data]

    opt.parse({'vocab_size':len(words),
               'embedding_length':embeddings.shape[1],
               'embeddings':embeddings})

    model = Bilstm_crf(opt, tag2label)
    optim = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()))
    if torch.cuda.is_available():
        model = model.cuda()
    x_train, x_valid, y_train, y_valid = train_test_split(sentence,tags,test_size=0.2)
    train_data = data_process.batch_iter(x_train, y_train, opt.batch_size, num_epochs=opt.num_epochs)
    steps = 0
    min_delta = opt.min_delta
    best_loss = None
    best_acc, best_pre, best_f1 = 0, 0, 0
    epoch_length = len(x_train)//opt.batch_size
    model.train()
    for idx, batch in enumerate(train_data):
        text = batch[0]
        text_lengths = [len(x) for x in text]
        max_len = max(text_lengths)
        pad_token = words_dict['<pad>']
        # 将每个batch的text pad到这个batch里最长的长度
        padded_text = np.ones((opt.batch_size, max_len)) * pad_token
        for i, x_len in enumerate(text_lengths):
            sequence = text[i]
            padded_text[i, 0:x_len] = sequence[:x_len]
        padded_text,text_lengths = data_process.pad_sequences(text)
        padded_tags,tag_lengths = data_process.pad_sequences(batch[1])
        padded_text = torch.from_numpy(padded_text).long()
        # 将label转成variable variable是吸纳了自动求导

        target = torch.from_numpy(padded_tags).long()
        target = torch.autograd.Variable(target).long()
        if torch.cuda.is_available():
            padded_text = padded_text.cuda()
            target = target.cuda()
        # 每个batch都将梯度归零，不累计
        optim.zero_grad()

        prediction = model(padded_text, text_lengths)
        loss = model.loss(padded_text, text_lengths, target)
        loss.backward()
        num_corrects = (prediction.data == target.data).float().sum()
        acc = num_corrects / len(batch[0])
        clip_gradient(model, 1e-1)
        optim.step()
        steps += 1
        if steps % epoch_length == 0:
            """每epoch,保存已经训练的模型"""
            print(torch.max(prediction, 1)[1].view(target.size()).data)
            print(
                f'epoch: {steps // epoch_length}, Idx: {idx + 1}, Training Loss: {loss.item():.4f}, Training Accuracy: {acc.item(): .2f}')
            # test_loss, test_acc, test_precision, test_recall, test_f1, test_logit = eval_model(model, x_valid, y_valid)
            # print(
            #     f'Val. Loss: {test_loss:3f}, Val. Acc: {test_acc:.3f}, Val.pre: {test_precision:.3f}, Val.recall: {test_recall:.3f},Val.f1: {test_f1:.3f}')
            # # define early stopping
            # if best_loss is None:
            #     best_loss = test_loss
            # elif test_loss - best_loss > -min_delta:  # 降低的loss不够阈值
            #     if wait >= opt.patience:
            #         print(f'Earlystopping in epoch ：{steps // epoch_length}')
            #         break
            #     wait += 1
            # else:
            #     wait = 1
            #     best_loss = test_loss
            #     best_acc = test_acc
            #     best_pre = test_precision
            #     best_f1 = test_f1
            #     print(f'best loss: {test_loss:.3f}, best acc: {test_acc:.3f}, best precision: {test_precision:.3f}')
            #     save_path = os.path.join(outdir, str(steps)) + '.pkl'
            #     save_dict = {"vocab_list": words, "state_dict": model.state_dict(), "optimizer": optim.state_dict(),
            #                  "epoch": steps // epoch_length}
            #     torch.save(save_dict, save_path)
    print(f'best loss: {best_loss:.3f}, best acc: {best_acc:.3f}, best precision: {best_pre:.3f}')
