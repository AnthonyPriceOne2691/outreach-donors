/**
 * Последняя цена донора, откуда она — и «Указать цену», которую человек знает
 * сам (07.10.2026).
 *
 * До 07.10.2026 цена попадала к донору только из разобранного ответа на письмо,
 * а агентство знает цены многих сайтов само — свой прайс, биржи, прошлые
 * сделки. Требование Этапа 2: «по кому запускаем — только доноры с известной
 * ценой (из базы или заведённые вручную)».
 *
 * **Откуда цена — строкой под ней**: из ответа донора или вписал человек — кто
 * и откуда он её знает. На цене держится оффер «мы дешевле» Этапа 2, и цена со
 * слов коллеги должна читаться как таковая. Дата — в строке цены: у ручной это
 * и есть день, когда её вписали, и второй раз она не повторяется.
 *
 * **«Указать цену» — у донора, принятого человеком.** Кандидата и отклонённого
 * решают в очереди прогона (туда ведёт шапка карточки); домен, которого нет
 * среди доноров, заводят вручную на панели «Обход доноров». Правило записи —
 * одно с панелью и консолью: отказ — словами сервера, ничего не записано.
 * Ответ — карточка целиком, как у адреса руками: цена и её источник
 * показываются словами сервера, а не догадкой экрана.
 *
 * **Отказ по состоянию донора — до нажатия** (08.10.2026): принятый донор
 * попал в стоп-лист или в поставщики, его домен — наш домен рассылки или зона,
 * где размещений не продают. Сервер называет это в карточке (`price_refusal`)
 * словами отказа «Записать цену» — кнопка заперта, причина под ней. Прежде
 * человек вписывал цену и узнавал об отказе нажатием.
 */

import { Button, Card, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { PERMISSION_TITLES } from '../api/labels';
import { setDonorPrice } from '../api/runs';
import type { DonorFullCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatDate, formatMoney } from '../format';
import { Unfold } from '../runs/Unfold';
import { ReplyOffers } from '../threads/ReplyOffers';
import { NEW_PRICE, PriceForm, priceBody, priceTyped } from './PriceForm';
import type { PriceDraft } from './PriceForm';

/** Откуда последняя цена — одной строкой: «Вручную: кто · откуда цена» или
 *  «Из ответа донора». Источника нет — цена записана до 07.10.2026, когда её
 *  давал только ответ. */
function priceOrigin(donor: DonorFullCard): string {
  if (donor.last_price_source !== 'manual') return 'Из ответа донора';
  const parts = [donor.last_price_by, donor.last_price_note].filter(Boolean);
  return parts.length === 0 ? 'Вручную' : `Вручную: ${parts.join(' · ')}`;
}

/** «Указать цену»: поля раскрываются кнопкой, отказ сервера — над ними. */
function SetPrice({ donor }: { donor: DonorFullCard }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<PriceDraft>(NEW_PRICE);
  const save = useMutation({
    mutationFn: () => setDonorPrice(donor.id, priceBody(draft)),
    onSuccess: (card) => {
      // Карточка — из ответа сервера; списку доноров пора перечитаться: цена
      // видна и в его строке.
      queryClient.setQueryData(['donor', String(donor.id)], card);
      void queryClient.invalidateQueries({ queryKey: ['donors'] });
      setDraft(NEW_PRICE);
      setOpen(false);
      // Донор, ставший негодным после обновления метрик, цену получает, а обход
      // его не берёт: это сказано, как у панели и консоли, — янтарём.
      const suitable = card.status === 'suitable';
      const money = formatMoney(card.last_price, card.last_price_currency);
      notifications.show({
        message: suitable
          ? `Цена записана: ${money}.`
          : `Цена записана: ${money}. По порогам отбора донор не годен — обход Этапа 2 его не возьмёт.`,
        color: suitable ? 'green' : 'yellow',
      });
    },
  });
  const formId = `donor-${donor.id}-price`;
  // Отказ по состоянию донора — словами сервера до нажатия: вписывать цену,
  // которую всё равно не запишут, незачем, и поля не раскрываются.
  const refusal = donor.price_refusal ?? null;
  const shown = open && refusal === null;
  return (
    <Stack gap="sm" mt="md" align="flex-start">
      <Button
        variant="default"
        className="press"
        aria-expanded={shown}
        aria-controls={formId}
        disabled={refusal !== null}
        onClick={() => {
          setOpen((was) => !was);
          save.reset();
        }}
      >
        Указать цену
      </Button>
      {refusal !== null ? <Text size="sm">{refusal}</Text> : null}
      <Unfold open={shown} w="100%">
        <PriceForm
          id={formId}
          draft={draft}
          onChange={(next) => {
            setDraft(next);
            save.reset();
          }}
          ready={priceTyped(draft)}
          pending={save.isPending}
          error={save.error}
          refused="Цену не записали"
          submit="Записать цену"
          onSubmit={() => save.mutate()}
          onCancel={() => setOpen(false)}
        />
      </Unfold>
    </Stack>
  );
}

export function DonorPrice({ donor }: { donor: DonorFullCard }) {
  const { can } = useSession();
  const isDonor = donor.review === 'accepted';
  // У записи без цены, которая не донор, показывать нечего: цену ей здесь
  // не указывают, а почему — говорит шапка карточки.
  if (donor.last_price === null && !isDonor) return null;
  return (
    <Card className="glass" p="xl">
      <Title order={5} mb="xs">
        Последняя цена
      </Title>
      {donor.last_price === null ? (
        <Text size="sm">Цены нет — по донору без цены Этап 2 не запускается.</Text>
      ) : (
        <>
          {/* Валюта приходит с ценой, а не подставляется здесь: конвертации
              в сервисе нет, и «USD» рядом с числом в евро — это не подпись,
              а неверное число. Деньги — общей функцией, как на остальных
              экранах: сырой строкой сервера цена печаталась «250.00 EUR». */}
          <Text>
            {formatMoney(donor.last_price, donor.last_price_currency)} ·{' '}
            {formatDate(donor.last_price_at)}
          </Text>
          <Text size="sm" c="dimmed">
            {priceOrigin(donor)}
          </Text>
        </>
      )}
      <ReplyOffers offers={donor.last_offers} />
      {/* Без права — объяснение на месте, а не пропавшая кнопка. */}
      {isDonor && can('run') ? <SetPrice donor={donor} /> : null}
      {isDonor && !can('run') ? (
        <Text size="sm" c="dimmed" mt="sm">
          Указать цену может сотрудник с правом «{PERMISSION_TITLES.run}».
        </Text>
      ) : null}
    </Card>
  );
}
