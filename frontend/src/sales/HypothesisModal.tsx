/**
 * «Новая гипотеза» — окно: имя и описание, кому и зачем пишем.
 *
 * До окна гипотеза заводилась только командой консоли, и мастер загрузки без неё не шёл.
 * Правило заведения одно — на сервере (`hypotheses.add`, то же у консоли): пробелы по краям
 * и двойные внутри имя новым не делают, занятое имя — отказ.
 *
 * **Отказ — до нажатия, где он ясен заранее, и словами сервера, где нет.** Пустое имя
 * видно у поля после правки или нажатия — кнопка не выключена молча; длину и занятое имя
 * судит сервер, его отказ встаёт над полями, а набранное остаётся на месте.
 *
 * Описание читают только экраны: в тексты писем оно не идёт. Хранится в базе, а не в
 * репозитории — это коммерческий текст.
 */

import { Alert, Button, Group, Modal, Stack, Textarea, TextInput } from '@mantine/core';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { HypothesisCard } from '../api/salesTypes';
import { ACTIONS_GAP, FIELD_GAP } from '../components/formRhythm';
import { useAddHypothesis } from './hypothesisData';

const NO_NAME = 'Впишите имя — по нему гипотезу выбирают при загрузке базы';

interface ModalProps {
  onClose: () => void;
  onAdded: (card: HypothesisCard) => void;
}

export function HypothesisModal({ onClose, onAdded }: ModalProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  // Пустая новая форма не краснеет сразу: «впишите» встаёт под полем после правки или нажатия.
  const [touched, setTouched] = useState(false);
  const add = useAddHypothesis(onAdded);
  const blank = name.trim() === '';
  // Пока сервер отвечает, окно не закрывается: его отказ иначе не увидели бы нигде.
  const close = () => {
    if (!add.isPending) onClose();
  };

  return (
    <Modal opened onClose={close} title="Новая гипотеза">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setTouched(true);
          if (blank) return;
          add.mutate({ name, description: description.trim() === '' ? null : description });
        }}
      >
        <Stack gap={FIELD_GAP}>
          {add.error !== null ? (
            <Alert color="red" title="Гипотеза не заведена">
              {refusalOf(add.error)}
            </Alert>
          ) : null}
          <TextInput
            label="Имя"
            description="Коротко: по нему гипотезу выбирают при загрузке базы и в отчётах"
            value={name}
            error={touched && blank ? NO_NAME : undefined}
            data-autofocus
            onChange={(event) => {
              setName(event.currentTarget.value);
              setTouched(true);
            }}
          />
          <Textarea
            label="Описание"
            description="Кому и зачем пишем — для своих; в письма не идёт"
            autosize
            minRows={3}
            maxRows={8}
            value={description}
            onChange={(event) => setDescription(event.currentTarget.value)}
          />
          <Group justify="flex-end" mt={ACTIONS_GAP}>
            <Button variant="subtle" className="press" disabled={add.isPending} onClick={close}>
              Отмена
            </Button>
            <Button type="submit" className="press" loading={add.isPending}>
              Завести
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}

/** Кнопка «Новая гипотеза» с окном. Не залитая: на вкладке гипотез залита «Загрузить базу»,
 *  в мастере — «Прочитать»; гипотезу заводят реже, чем грузят базу. */
export function NewHypothesisButton({ onAdded }: { onAdded?: (card: HypothesisCard) => void }) {
  const [opened, setOpened] = useState(false);
  return (
    <>
      <Button variant="default" className="press" onClick={() => setOpened(true)}>
        Новая гипотеза
      </Button>
      {opened ? (
        <HypothesisModal
          onClose={() => setOpened(false)}
          onAdded={(card) => {
            setOpened(false);
            onAdded?.(card);
          }}
        />
      ) : null}
    </>
  );
}
