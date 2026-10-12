/**
 * «Цена та же» — у ответа донора на перезапрос цены без числа (12.10.2026).
 *
 * Перезапрос просит назвать цену числом, но ответ бывает «цена та же», «всё по-прежнему».
 * Разбор такой ответ в карточку не кладёт, и переписка ждёт человека, а не пишет «цена
 * получена» старой ценой. Решение человека — эта кнопка: прежняя цена донора ложится
 * в карточку временем ответа и снова свежая, а в журнале — кто и по какому ответу
 * (`letters/recheck_answer.py`). Назвал донор новую — её вписывают в поля разбора, как всегда.
 *
 * **С подтверждением у кнопки** (`ConfirmPopover`): решение не отзывается — передумав,
 * правят цену в разборе. **Отказ — под кнопкой, словами сервера**, как у разбора цены:
 * уведомление в углу закрывало кнопки (проверка QA 10.10.2026).
 */

import { Button, Group, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { confirmSamePrice } from '../api/priceRecheck';
import type { IncomingCard } from '../api/types';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { formatMoney } from '../format';
import { notify } from '../notices';
import { Plaque } from './Plaque';

export function SamePrice({ incoming, canReview }: { incoming: IncomingCard; canReview: boolean }) {
  const queryClient = useQueryClient();
  const confirm = useMutation({
    mutationFn: () => confirmSamePrice(incoming.id),
    onSuccess: async (done) => {
      // Переписка и список: ответ больше не ждёт человека, цена переписки — снова свежая.
      await queryClient.invalidateQueries({ queryKey: ['thread'] });
      await queryClient.invalidateQueries({ queryKey: ['threads'] });
      notify({
        message: done.stored
          ? 'Прежняя цена подтверждена ответом донора и снова свежая'
          : 'Подтверждено. В карточку донора не легло: там слово новее этого ответа',
        color: done.stored ? 'green' : 'yellow',
      });
    },
  });
  const same = incoming.same_price;
  if (same === undefined || same === null || !canReview) return null;
  const money = formatMoney(same.price, same.currency);
  return (
    <Stack gap={6}>
      <Group gap="xs" align="center">
        <ConfirmPopover
          message={`Донор подтвердил прежнюю цену ${money}? Она ляжет в карточку временем его ответа и станет свежей.`}
          confirm="Подтвердить"
          onConfirm={() => confirm.mutate()}
        >
          {(ask) => (
            <Button
              variant="light"
              className="press"
              px="md"
              loading={confirm.isPending}
              onClick={ask}
            >
              Цена та же · {money}
            </Button>
          )}
        </ConfirmPopover>
        <Text size="xs" c="dimmed">
          ответ на перезапрос без числа: донор подтвердил прежнюю — эта кнопка, назвал новую — поля
          выше
        </Text>
      </Group>
      {confirm.isError ? (
        <Plaque color="red" title="Не подтвердили">
          — {refusalOf(confirm.error)}
        </Plaque>
      ) : null}
    </Stack>
  );
}
