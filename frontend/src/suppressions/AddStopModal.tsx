/**
 * «Больше не писать»: запись в стоп-лист руками.
 *
 * **Окно открывается чистым: вручную, навсегда** (проверка QA 10.10.2026). Поля
 * жили в экране, и после записи сбрасывался один адрес: «поставщик» и «12 месяцев»
 * прошлой записи доставались следующей, и она молча ложилась поставщиком на год —
 * а через год переставала держать. Теперь поля живут здесь, а экран монтирует окно
 * заново на каждое открытие: поле, добавленное сюда завтра, сбросится так же,
 * без отдельной строки сброса.
 *
 * **Enter — то же, что кнопка**: поля в форме, кнопка — её отправка, и запрет
 * короткого ввода у них один.
 */

import { Button, Group, Modal, SegmentedControl, Stack, Text, TextInput } from '@mantine/core';
import type { NotificationData } from '@mantine/notifications';
import { useState } from 'react';

import type { StopAdded } from '../api/outreach';
import type { SuppressionReason } from '../api/types';

const HAND_REASONS: { value: SuppressionReason; label: string }[] = [
  { value: 'manual', label: 'вручную' },
  { value: 'supplier', label: 'поставщик' },
];

/** Сроки записи. «Навсегда» первым: оно и есть умолчание. */
const TERMS = [
  { value: 'forever', label: 'навсегда' },
  { value: 'year', label: '12 месяцев' },
] as const;

type Term = (typeof TERMS)[number]['value'];

/** Короче домена не бывает: `a.b`. Кнопка и Enter молчат до третьего знака. */
const SHORTEST = 3;

/** Когда запись перестаёт держать. Год считается от сегодня — так
 *  требование и называет поставщиков: «размещались за последние 12 мес.». */
function endOf(term: Term): string | null {
  if (term === 'forever') return null;
  const until = new Date();
  until.setFullYear(until.getFullYear() + 1);
  return until.toISOString();
}

export interface StopRequest {
  target: string;
  reason: SuppressionReason;
  expires_at: string | null;
}

/** Что сказать о новой записи. Домен, которого в базе не было, — не закрытый
 *  донор: так выглядели опечатка и поддомен, на который донор не записан
 *  (проверка QA 10.10.2026), и «письма сняты с очереди» здесь было неправдой.
 *  Такое уведомление не гаснет само: оно просит сверить домен, а не сообщает. */
export function addedNotice(row: StopAdded): NotificationData {
  if (row.new_domain) {
    return {
      title: 'Записан новый домен',
      message: `${row.host} в базе не было — донора с таким доменом нет. Закрывали донора — сверьте домен с его карточкой`,
      color: 'yellow',
      autoClose: false,
    };
  }
  return {
    message: `${row.host ?? row.email} в стоп-листе — письма сняты с очереди`,
    color: 'green',
  };
}

interface Props {
  pending: boolean;
  onClose: () => void;
  onSubmit: (request: StopRequest) => void;
}

export function AddStopModal({ pending, onClose, onSubmit }: Props) {
  const [target, setTarget] = useState('');
  const [reason, setReason] = useState<SuppressionReason>('manual');
  const [term, setTerm] = useState<Term>('forever');
  const ready = target.trim().length >= SHORTEST && !pending;

  return (
    <Modal opened onClose={onClose} title="Больше не писать">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) onSubmit({ target: target.trim(), reason, expires_at: endOf(term) });
        }}
      >
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            Домен закрывает сайт целиком: ссылку, www. и поддомен сводим к домену сайта. Адрес —
            один ящик. Домен, которого ещё нет в базе, заводится вместе с записью: список
            поставщиков приходит раньше первого прогона.
          </Text>
          <TextInput
            label="Домен или адрес"
            placeholder="site.com или editor@site.com"
            value={target}
            onChange={(event) => setTarget(event.currentTarget.value)}
            data-autofocus
          />
          {/* Два значения — переключателем, а не списком в 200 px (аудит 09.10.2026). */}
          <Stack gap={4}>
            <Text size="sm" fw={500}>
              Причина
            </Text>
            <SegmentedControl
              aria-label="Причина"
              data={HAND_REASONS}
              value={reason}
              onChange={(picked) => setReason(picked as SuppressionReason)}
              style={{ alignSelf: 'flex-start' }}
            />
          </Stack>
          <Stack gap={4}>
            <Text size="sm" fw={500}>
              Держит
            </Text>
            <SegmentedControl
              aria-label="Держит"
              data={TERMS.map((item) => ({ value: item.value, label: item.label }))}
              value={term}
              onChange={(picked) => setTerm(picked as Term)}
              style={{ alignSelf: 'flex-start' }}
            />
          </Stack>
          <Group justify="flex-end" gap="sm">
            <Button variant="default" onClick={onClose}>
              Отмена
            </Button>
            <Button type="submit" loading={pending} disabled={target.trim().length < SHORTEST}>
              Больше не писать
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
