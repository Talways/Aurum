import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input, Label } from "@/components/ui/Input";
import { useSetAccountBalance } from "@/hooks/useAccounts";
import { useTranslation } from "@/lib/i18n";
import type { AccountWithBalance } from "@/types";

interface AccountBalanceModalProps {
  open: boolean;
  onClose: () => void;
  account: AccountWithBalance | null;
}

export function AccountBalanceModal({ open, onClose, account }: AccountBalanceModalProps) {
  const { t } = useTranslation();
  const setAccountBalance = useSetAccountBalance();
  const [balance, setBalance] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !account) return;
    setBalance(account.balance);
    setError(null);
  }, [open, account]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!account) return;
    setError(null);
    try {
      await setAccountBalance.mutateAsync({ id: account.id, balance });
      onClose();
    } catch {
      setError(t("account.balance.saveError"));
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={t("account.balance.title")}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <p className="text-sm text-text-secondary">
          {t("account.balance.explanation", { name: account?.name ?? "" })}
        </p>
        <div>
          <Label htmlFor="account-balance">{t("account.balance.amountLabel")}</Label>
          <Input
            id="account-balance"
            type="number"
            step="0.01"
            required
            value={balance}
            onChange={(event) => setBalance(event.target.value)}
          />
        </div>
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" disabled={setAccountBalance.isPending}>
            {setAccountBalance.isPending ? t("common.saving") : t("common.save")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
