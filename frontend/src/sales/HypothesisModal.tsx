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
 *
 * **В окне одна прокрутка** (`OneScrollForm`): описание с потолком в 8 строк прокручивалось
 * внутри окна, а на низком экране (844 × 390) окно прокручивалось и само, и «Завести» уезжала
 * под край. Теперь описание растёт по тексту, а прокручивается середина окна.
 */

import { Alert, Button, Group, Modal, Textarea, TextInput } from '@mantine/core';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { HypothesisCard } from '../api/salesTypes';
import { ACTIONS_GAP, FIELD_GAP } from '../components/formRhythm';
import { useAddHypothesis } from './hypothesisData';
import { OneScrollForm } from './OneScrollForm';

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
    <Modal opened onClose={close} title="Новая гипотеза" classNames={{ content: 'oneScroll' }}>
      <OneScrollForm
        gap={FIELD_GAP}
        onSubmit={(event) => {
          event.preventDefault();
          setTouched(true);
          if (blank) return;
          add.mutate({ name, description: description.trim() === '' ? null : description });
        }}
        top={
          add.error !== null && (
            <Alert color="red" title="Гипотеза не заведена">
              {refusalOf(add.error)}
            </Alert>
          )
        }
        footer={
          <Group justify="flex-end" mt={ACTIONS_GAP}>
            <Button variant="subtle" className="press" disabled={add.isPending} onClick={close}>
              Отмена
            </Button>
            <Button type="submit" className="press" loading={add.isPending}>
              Завести
            </Button>
          </Group>
        }
      >
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
          description="Кому и зачем пишем: в письма не идёт, по нему судья сбора базы проверяет сайты"
          // Без `maxRows`: поле длиннее потолка прокручивалось само — вторая прокрутка в окне.
          autosize
          minRows={3}
          value={description}
          onChange={(event) => setDescription(event.currentTarget.value)}
        />
      </OneScrollForm>
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
