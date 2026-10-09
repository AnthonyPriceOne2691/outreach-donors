/**
 * Разбор цены под текстом ответа: что увидела модель и что решил человек.
 *
 * **Поля стоят рядом с исходным текстом, а не вместо него.** Проверить
 * спорный разбор можно только сравнением: человек читает письмо и видит,
 * откуда взялось число. Карточка, показывающая одно число вместо письма,
 * проверке не поддаётся вовсе.
 *
 * **Уверенность модели показана, а не спрятана.** «Уверенность 40%» —
 * это и есть объяснение, почему цена не попала в базу сама.
 *
 * **Подтверждение человека сильнее модели.** Нажав «подтвердить», он
 * кладёт цену в карточку донора независимо от того, что насчитала
 * модель. Обратного действия нет намеренно: передумав, он правит цифры
 * и подтверждает снова — это честнее, чем «отменить подтверждение»,
 * после которого непонятно, какое число считается верным.
 *
 * **Причина — от сервера, если она не «уверенности не хватило».** Автоответ
 * с суммой модель не разбирала вовсе: объяснение про уверенность было бы
 * неправдой, и человек искал бы разбор, которого нет.
 */

import { Alert, Badge, Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { useEffect, useState } from 'react';

import type { IncomingCard } from '../api/types';

export interface PriceReviewProps {
  incoming: IncomingCard;
  canReview: boolean;
  busy: boolean;
  onConfirm: (values: {
    price_white: string | null;
    price_grey: string | null;
    currency: string | null;
  }) => void;
  /** Донор ответил «не продаём размещения». Для гест-постинга это ответ на
   *  главный вопрос письма: он уходит в отбор, и домен выходит из прогонов. */
  onDecline: () => void;
}

function clean(value: string): string | null {
  const trimmed = value.trim().replace(',', '.');
  return trimmed === '' ? null : trimmed;
}

export function PriceReview({ incoming, canReview, busy, onConfirm, onDecline }: PriceReviewProps) {
  const [white, setWhite] = useState(incoming.price_white ?? '');
  const [grey, setGrey] = useState(incoming.price_grey ?? '');
  const [currency, setCurrency] = useState(incoming.currency ?? '');

  // Смена ответа сбрасывает поля: иначе цена одного донора уехала бы
  // в карточку другого.
  useEffect(() => {
    setWhite(incoming.price_white ?? '');
    setGrey(incoming.price_grey ?? '');
    setCurrency(incoming.currency ?? '');
  }, [incoming.id, incoming.price_white, incoming.price_grey, incoming.currency]);

  return (
    <Stack gap="sm" mt="sm">
      {(incoming.confidence !== null || incoming.reviewed_by !== null) && (
        <Group gap="sm">
          {incoming.confidence !== null && (
            <Text size="xs" c="dimmed">
              уверенность разбора {(incoming.confidence * 100).toFixed(0)}%
            </Text>
          )}
          {incoming.reviewed_by !== null && (
            <Badge variant="light" color="green">
              подтвердил {incoming.reviewed_by}
            </Badge>
          )}
        </Group>
      )}

      {incoming.placement === 'declines' && !incoming.needs_review && (
        <Badge variant="light" color="gray">
          донор: размещений не продаёт
        </Badge>
      )}

      {/* Одним абзацем, без строки заголовка: рядом со списком переписка — колонка
          высотой окна, и плашка в три строки уводила поля цены под край (09.10.2026). */}
      {incoming.needs_review && (
        <Alert color="yellow" py="xs" px="sm">
          <Text span size="sm" fw={600}>
            Цену подтверждает человек
          </Text>{' '}
          <Text span size="sm">
            {incoming.review_reason
              ? `— причина: ${incoming.review_reason}. Впишите цену из текста письма выше и подтвердите.`
              : '— уверенности разбора не хватило, чтобы положить цену в карточку донора. Сверьте числа с текстом письма выше и подтвердите или поправьте.'}
          </Text>
        </Alert>
      )}

      {/* `fieldRow` резервирует место под пояснение: без него подписи
          полей встают на разной высоте, и ряд «пляшет» — видно на снимке,
          где «с пометкой «партнёрский материал»» занимает две строки. */}
      <Group gap="sm" align="flex-end" className="fieldRow">
        <TextInput
          label="Белая цена"
          description="с пометкой «партнёрский материал»"
          value={white}
          w={170}
          disabled={!canReview}
          onChange={(event) => setWhite(event.currentTarget.value)}
        />
        <TextInput
          label="Серая цена"
          description="без пометки"
          value={grey}
          w={170}
          disabled={!canReview}
          onChange={(event) => setGrey(event.currentTarget.value)}
        />
        <TextInput
          label="Валюта"
          description="как назвали"
          value={currency}
          w={120}
          disabled={!canReview}
          onChange={(event) => setCurrency(event.currentTarget.value)}
        />
        <Button
          color="lagoon"
          className="press"
          loading={busy}
          disabled={!canReview}
          onClick={() =>
            onConfirm({
              price_white: clean(white),
              price_grey: clean(grey),
              currency: clean(currency),
            })
          }
        >
          Подтвердить
        </Button>
        {/* Отдельной кнопкой, а не пустыми полями: «цены нет в письме» и
            «донор сказал, что не продаёт» — разные ответы, и второй убирает
            домен из отбора на год. */}
        <Button
          variant="default"
          className="press"
          disabled={!canReview || busy}
          onClick={onDecline}
        >
          Не продаёт размещения
        </Button>
      </Group>

      {!canReview && (
        <Text size="xs" c="dimmed">
          Подтверждение цены — отдельное действие, его выдаёт админ. Смотреть разбор можно всем.
        </Text>
      )}
    </Stack>
  );
}
