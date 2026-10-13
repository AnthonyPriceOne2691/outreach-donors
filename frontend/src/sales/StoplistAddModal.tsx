/**
 * Окно «Добавить в стоп-лист продаж»: список клиентов и партнёров — разом, по строке, или
 * файлом CSV, как у консоли.
 *
 * **Что не легло — по строке, остальное принято** (`remarks` сервера): «не домен и не адрес»,
 * домен бесплатной почты. Тогда окно остаётся открытым: сверху — итог и замечания, в поле —
 * только непринятые строки, их правят и добавляют ещё раз. Всё легло — окно закрывается, итог —
 * уведомлением.
 *
 * **Пополнение снимает лидов**, которых закрыли новые записи, — сервер делает это правилом
 * очистки и называет число; окно говорит об этом до нажатия — строкой над кнопками, а не в
 * прокрутке: длинный список отодвигал бы её из виду.
 *
 * **В окне одна прокрутка** (`OneScrollForm`): поле списка с потолком в 14 строк прокручивалось
 * внутри окна, которое прокручивалось само, — кнопки уезжали под край экрана уже на
 * 1280 × 720. Теперь поле растёт по списку; замечания после «Добавить» встают в начале той же
 * прокрутки и подъезжают на виду (`useReveal`).
 */

import { Alert, FileInput, List, Modal, Stack, Text, TextInput, Textarea } from '@mantine/core';
import { IconUpload } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { addToSalesStoplist } from '../api/salesStoplist';
import type { SalesStoplistAdded, StoplistInput } from '../api/salesStoplist';
import { formatNumber } from '../format';
import { OneScrollForm, useReveal } from './OneScrollForm';
import { SaveActions } from './SaveActions';
import { SaveRefusal } from './SaveRefusal';

/** Итог пополнения словами — для уведомления и для окна с замечаниями. */
export function addedLine(result: SalesStoplistAdded): string {
  const closed = result.closed > 0 ? `; лидов снято: ${formatNumber(result.closed)}` : '';
  return (
    `Стоп-лист продаж: добавлено ${formatNumber(result.added)}, ` +
    `уже было ${formatNumber(result.known)}${closed}.`
  );
}

interface Props {
  /** Предел причины — сервера. */
  noteMax: number;
  onClose: () => void;
  /** Всё легло: окно закрывается, итог — словами. */
  onDone: (result: SalesStoplistAdded) => void;
  /** Что-то легло, а что-то нет: список и лиды перечитать, окно — оставить. */
  onPartly: () => void;
}

function Remarks({ result }: { result: SalesStoplistAdded }) {
  return (
    <Alert color="yellow" title="Не все строки легли">
      <Stack gap={6}>
        <Text size="sm">
          {addedLine(result)} Непринятые строки остались в поле — поправьте и добавьте ещё раз.
        </Text>
        <List size="sm" spacing={2}>
          {result.remarks.map((remark) => (
            <List.Item key={`${remark.line}:${remark.cell}`}>
              строка {remark.line}: {remark.reason} — «{remark.cell}»
            </List.Item>
          ))}
        </List>
      </Stack>
    </Alert>
  );
}

export function StoplistAddModal({ noteMax, onClose, onDone, onPartly }: Props) {
  const [input, setInput] = useState<StoplistInput>({ text: '', file: null, note: '' });
  const [partly, setPartly] = useState<SalesStoplistAdded | null>(null);
  const remarksRef = useReveal(partly);
  const add = useMutation({
    mutationFn: addToSalesStoplist,
    onSuccess: (result) => {
      if (result.remarks.length === 0) {
        onDone(result);
        return;
      }
      setPartly(result);
      setInput((current) => ({
        ...current,
        file: null,
        text: result.remarks.map((remark) => remark.cell).join('\n'),
      }));
      onPartly();
    },
  });
  const longNote = input.note.trim().length > noteMax;
  const given = input.file !== null || input.text.trim() !== '';

  return (
    <Modal
      opened
      onClose={onClose}
      size="lg"
      title="Добавить в стоп-лист продаж"
      classNames={{ content: 'oneScroll' }}
    >
      <OneScrollForm
        onSubmit={(event) => {
          event.preventDefault();
          if (given && !longNote) add.mutate(input);
        }}
        top={add.error !== null && <SaveRefusal error={add.error} />}
        footer={
          <>
            <Text size="sm" c="dimmed">
              Лиды, которых закроют новые записи, снимутся сразу — той же проверкой, что у очистки.
            </Text>
            <SaveActions
              ready={given && !longNote}
              busy={add.isPending}
              onCancel={onClose}
              label="Добавить"
            />
          </>
        }
      >
        {partly !== null && (
          <div ref={remarksRef}>
            <Remarks result={partly} />
          </div>
        )}
        <Textarea
          label="Адреса и домены — по строке"
          description="Домен закрывает всех на нём; адрес — одного человека. Из таблицы можно вставить колонку целиком."
          placeholder={'acme.example\nboss@partner.example'}
          // Без `maxRows`: поле длиннее потолка прокручивалось само — вторая прокрутка в окне.
          autosize
          minRows={5}
          value={input.text}
          disabled={input.file !== null}
          onChange={(event) => setInput({ ...input, text: event.currentTarget.value })}
        />
        <FileInput
          label="Или файл CSV"
          description="В строке — домен или адрес, как у консоли; первая строка может быть заголовком."
          placeholder="выберите файл"
          accept=".csv,text/csv,text/plain"
          leftSection={<IconUpload size={16} />}
          clearable
          value={input.file}
          onChange={(file) => setInput({ ...input, file })}
        />
        <TextInput
          label="Причина"
          description="Кто они вам: клиенты, партнёры. Видна в списке."
          placeholder="клиенты агентства"
          value={input.note}
          error={longNote ? `Не длиннее ${formatNumber(noteMax)} знаков` : undefined}
          onChange={(event) => setInput({ ...input, note: event.currentTarget.value })}
        />
      </OneScrollForm>
    </Modal>
  );
}
