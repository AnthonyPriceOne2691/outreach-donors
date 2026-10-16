/**
 * Окно гипотезы: «Новая гипотеза» — имя и описание, кому и зачем пишем, — и то же окно в
 * режиме правки («Править» в строке вкладки «Гипотезы»), со «Удалить гипотезу» под полями.
 *
 * До окна гипотеза заводилась только командой консоли, и мастер загрузки без неё не шёл.
 * Правило одно — на сервере (`hypotheses.add` и `hypotheses.edit`, то же у консоли): пробелы по
 * краям и двойные внутри имя новым не делают, занятое имя — отказ, имя рассылки продаж — тоже.
 *
 * **Отказ — до нажатия, где он ясен заранее, и словами сервера, где нет.** Пустое имя
 * видно у поля после правки или нажатия — кнопка не выключена молча; длину и занятое имя
 * судит сервер, его отказ встаёт над полями, а набранное остаётся на месте.
 *
 * **Правка шлёт только поправленное**: не тронутое описание не уходит и не затирает правку,
 * сделанную на «Сборе базы», пока окно было открыто. Ничего не поправили — окно просто
 * закрывается. Что зависит от имени и от описания, — в «i» у подписей: имя называет рассылку
 * гипотезы и уходит меткой новых сделок в Kommo, описание — вопрос судье сбора базы.
 *
 * Описание читают только экраны и судья сбора базы: в тексты писем оно не идёт. Хранится в
 * базе, а не в репозитории — это коммерческий текст.
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
import { HintLabel } from '../components/HintLabel';
import { useAddHypothesis, useEditHypothesis, useRemoveHypothesis } from './hypothesisData';
import type { HypothesisEdit } from './hypothesisData';
import { HypothesisRemove } from './HypothesisRemove';
import { OneScrollForm } from './OneScrollForm';

const NO_NAME = 'Впишите имя — по нему гипотезу выбирают при загрузке базы';
const NAME_HINT =
  'Новое имя получит и рассылка гипотезы. Новые сделки в Kommo и сообщения телемаркетологу придут с новым именем, прежние останутся с прежним.';
const SEGMENT_HINT =
  'Правка описания — новый вопрос судье сбора базы: смета сбора снимется, прежние вердикты по нему не годятся.';

/** Что поправили — только это и уходит. Пробелы имя новым не делают, как на сервере. */
function changes(card: HypothesisCard, name: string, description: string): HypothesisEdit {
  const edit: HypothesisEdit = {};
  if (name.split(/\s+/).filter(Boolean).join(' ') !== card.name) edit.name = name;
  if (description.trim() !== (card.description ?? '')) edit.description = description;
  return edit;
}

interface Refusal {
  error: Error | null;
  title: string;
}

/** Какой отказ показать над полями: заведения — или, в правке, удаления, а нет его — правки. */
function shownRefusal(
  editing: boolean,
  add: Refusal['error'],
  save: Refusal['error'],
  remove: Refusal['error'],
): Refusal {
  if (!editing) return { error: add, title: 'Гипотеза не заведена' };
  if (remove !== null) return { error: remove, title: 'Гипотеза не удалена' };
  return { error: save, title: 'Гипотеза не сохранена' };
}

interface FieldsProps {
  /** Окно правки: у подписей — «i», что зависит от имени и описания. */
  editing: boolean;
  name: string;
  description: string;
  nameError: string | undefined;
  onName: (value: string) => void;
  onDescription: (value: string) => void;
}

function HypothesisFields({
  editing,
  name,
  description,
  nameError,
  onName,
  onDescription,
}: FieldsProps) {
  return (
    <>
      <TextInput
        label={editing ? <HintLabel label="Имя" hint={NAME_HINT} /> : 'Имя'}
        description="Коротко: по нему гипотезу выбирают при загрузке базы и в отчётах"
        value={name}
        error={nameError}
        data-autofocus
        onChange={(event) => onName(event.currentTarget.value)}
      />
      <Textarea
        label={editing ? <HintLabel label="Описание" hint={SEGMENT_HINT} /> : 'Описание'}
        description="Кому и зачем пишем: в письма не идёт, по нему судья сбора базы проверяет сайты"
        // Без `maxRows`: поле длиннее потолка прокручивалось само — вторая прокрутка в окне.
        autosize
        minRows={3}
        value={description}
        onChange={(event) => onDescription(event.currentTarget.value)}
      />
    </>
  );
}

interface FooterProps {
  /** Окно правки: главная кнопка — «Сохранить», а не «Завести». */
  editing: boolean;
  busy: boolean;
  /** Ждёт ответа заведение или правка: крутится главная кнопка. */
  saving: boolean;
  onCancel: () => void;
}

/** Кнопки окна: «Отмена» и главная. «Удалить гипотезу» — не здесь, а под полями
 *  (`HypothesisRemove`): рядом с главной кнопкой разрушительной не место. */
function HypothesisFooter({ editing, busy, saving, onCancel }: FooterProps) {
  return (
    <Group justify="flex-end" mt={ACTIONS_GAP}>
      <Button variant="subtle" className="press" disabled={busy} onClick={onCancel}>
        Отмена
      </Button>
      <Button type="submit" className="press" loading={saving}>
        {editing ? 'Сохранить' : 'Завести'}
      </Button>
    </Group>
  );
}

interface ModalProps {
  /** Гипотеза, которую правят; нет — окно заводит новую. */
  card?: HypothesisCard;
  onClose: () => void;
  onAdded?: (card: HypothesisCard) => void;
  /** Гипотеза удалена: снять её из адреса раздела. */
  onRemoved?: (card: HypothesisCard) => void;
}

/** Запросы окна — заведение, правка, удаление — и что из них ждёт ответа. */
function useRequests({ card, onClose, onAdded, onRemoved }: ModalProps) {
  const add = useAddHypothesis((made) => onAdded?.(made));
  const save = useEditHypothesis(() => onClose());
  const remove = useRemoveHypothesis((gone) => {
    onRemoved?.(gone);
    onClose();
  });
  return {
    add,
    save,
    remove,
    busy: add.isPending || save.isPending || remove.isPending,
    saving: add.isPending || save.isPending,
    refusal: shownRefusal(card !== undefined, add.error, save.error, remove.error),
  };
}

export function HypothesisModal(props: ModalProps) {
  const { card, onClose } = props;
  const [name, setName] = useState(card?.name ?? '');
  const [description, setDescription] = useState(card?.description ?? '');
  // Пустая новая форма не краснеет сразу: «впишите» встаёт под полем после правки или нажатия.
  const [touched, setTouched] = useState(false);
  const { add, save, remove, busy, saving, refusal } = useRequests(props);
  const blank = name.trim() === '';
  // Пока сервер отвечает, окно не закрывается: его отказ иначе не увидели бы нигде.
  const close = () => {
    if (!busy) onClose();
  };

  const submit = () => {
    setTouched(true);
    if (blank) return;
    if (card === undefined) {
      add.mutate({ name, description: description.trim() === '' ? null : description });
      return;
    }
    const edit = changes(card, name, description);
    remove.reset();
    if (Object.keys(edit).length === 0) onClose();
    else save.mutate({ id: card.id, body: edit });
  };

  return (
    <Modal
      opened
      onClose={close}
      title={card === undefined ? 'Новая гипотеза' : `Гипотеза «${card.name}»`}
      classNames={{ content: 'oneScroll' }}
    >
      <OneScrollForm
        gap={FIELD_GAP}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        top={
          refusal.error !== null && (
            <Alert color="red" title={refusal.title}>
              {refusalOf(refusal.error)}
            </Alert>
          )
        }
        footer={
          <HypothesisFooter
            editing={card !== undefined}
            busy={busy}
            saving={saving}
            onCancel={close}
          />
        }
      >
        <HypothesisFields
          editing={card !== undefined}
          name={name}
          description={description}
          nameError={touched && blank ? NO_NAME : undefined}
          onName={(value) => {
            setName(value);
            setTouched(true);
          }}
          onDescription={setDescription}
        />
        {card !== undefined && (
          <HypothesisRemove
            card={card}
            busy={busy}
            onRemove={() => {
              save.reset();
              remove.mutate(card);
            }}
          />
        )}
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
