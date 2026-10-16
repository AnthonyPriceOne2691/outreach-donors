/**
 * «Удалить гипотезу» — последним блоком окна гипотезы в режиме правки, под полями, с причиной
 * прямо под кнопкой. На случай, если завели по ошибке.
 *
 * Удаляется только пустая (решение владельца 15.10): лиды, диалоги и письма, прогоны сбора,
 * свой набор цепочки держат гипотезу. **Что держит, окно читает при открытии**
 * (`GET /sales/hypotheses/{N}/holds`, свежим): непустая — кнопка видна, но закрыта, и под ней
 * строкой — что держит, словами сервера с числами (правило «причина выключенной кнопки — строкой
 * под ней»). Блоком под полями, а не в подвале рядом с «Сохранить»: в подвале длинные слова
 * рвались колонкой у кнопки и сталкивали «Отмену» и «Сохранить» строкой ниже, а на 390 px
 * причина уезжала под чужие кнопки (снимки стенда 15.10.2026); и разрушительная кнопка не стоит
 * вплотную к главной. Пустая — подтверждение у самой кнопки (`ConfirmPopover`): вопрос называет
 * гипотезу по имени, фокус — на «Отмене», на кнопке — глагол.
 *
 * Сервер считает заново под блокировкой строки: лид, заведённый в ту же секунду, вернёт 409
 * словами — их окно показывает над полями, а кнопка закрывается с новыми словами.
 */

import { Button, Loader, Stack, Text } from '@mantine/core';

import { refusalOf } from '../api/client';
import type { HypothesisCard } from '../api/salesTypes';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { useHolds } from './hypothesisData';

/** Почему удалить нельзя — строкой под кнопками; нечего сказать — ничего. */
function Reason({ held, error }: { held: string | null; error: Error | null }) {
  const said =
    error === null
      ? held
      : `Что держит гипотезу, не прочитано — удалить пока нельзя: ${refusalOf(error)}`;
  return said === null ? null : (
    <Text size="xs" c="dimmed">
      {said}
    </Text>
  );
}

interface Props {
  card: HypothesisCard;
  /** Окно ждёт ответа сервера — правки или удаления: второе нажатие не уходит. */
  busy: boolean;
  onRemove: () => void;
}

export function HypothesisRemove({ card, busy, onRemove }: Props) {
  const holds = useHolds(card.id);
  const held = holds.data?.held ?? null;
  const open = holds.data !== undefined && held === null && !busy;
  return (
    <Stack gap={4} align="flex-start" mt="xs" className="hypothesisRemove">
      {holds.isPending ? (
        <Loader size="xs" aria-label="Проверяем, что держит гипотезу" />
      ) : (
        <ConfirmPopover
          message={`Удалить гипотезу «${card.name}»? Лидов, прогонов сбора и писем у неё нет. Имя и описание останутся в журнале действий.`}
          confirm="Удалить"
          danger
          onConfirm={onRemove}
        >
          {(ask) => (
            <Button variant="subtle" color="red" className="press" disabled={!open} onClick={ask}>
              Удалить гипотезу
            </Button>
          )}
        </ConfirmPopover>
      )}
      <Reason held={held} error={holds.error} />
    </Stack>
  );
}
